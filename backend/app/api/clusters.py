"""
Clusters API endpoints
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, Request, status, Query
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime

from app.models.cluster import Cluster, Node, DatabaseUser, Collection, CollectionIndex
from app.models.mongo_backup import MongoDatabaseBackup
from app.services.cache import CacheService
from app.services.celery_app import MONITOR_SPECS, celery_app
from app.services.mongo_collector import MongoCollector
from app.services.mongo_backup_exporter import get_mongo_backup_exporter_client
from app.api.auth import get_optional_user, require_permission
from app.audit import audit_log
from app.authorization import has_permission
from app.monitor_logging import log_collector_progress, log_monitor_request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/clusters", tags=["clusters"])

cache = CacheService()
collector = MongoCollector()


class MongoStepDownRequest(BaseModel):
    stepdown_seconds: int = Field(default=60, ge=5, le=600)


class MongoStepDownResponse(BaseModel):
    status: str
    cluster: str
    previous_primary: str
    stepdown_seconds: int
    refresh_task_id: Optional[str] = None


@router.get("", response_model=List[str])
async def get_clusters():
    """Get list of all clusters"""
    from app.config import get_app_config
    current_config = get_app_config()
    if not current_config:
        logger.warning("App config not initialized in get_clusters endpoint")
        return []
    cluster_names = [cluster.name for cluster in current_config.clusters]
    logger.debug(f"Returning cluster names: {cluster_names}")
    return cluster_names


@router.get("/databases-overview")
async def get_databases_overview():
    """Get cluster -> databases map from cache for MongoDB search."""
    return cache.get_databases_overview()


@router.get("/{cluster_name}", response_model=Cluster)
async def get_cluster(
    cluster_name: str,
    current_user: Optional[dict] = Depends(get_optional_user)
):
    """Get cluster details"""
    from app.config import get_app_config
    current_config = get_app_config()
    if not current_config:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Конфигурация пока не загружена."
        )
    if not current_config.get_cluster_by_name(cluster_name):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Кластер {cluster_name} не найден."
        )

    # Try to get from cache first
    cached_data = cache.get_cluster_data(cluster_name)
    logger.debug(f"Cache lookup for {cluster_name}: {'found' if cached_data else 'not found'}")

    if not cached_data:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Данные кластера {cluster_name} временно недоступны. Фоновый сбор выполняется по расписанию."
        )

    # Build response (enrich nodes with lag_history from Redis)
    lag_histories = cache.get_lag_history(cluster_name)
    nodes = []
    for node_data in cached_data.get('nodes', []):
        node_data = dict(node_data)
        node_data['lag_history'] = lag_histories.get(node_data.get('name', ''), [])
        nodes.append(Node(**node_data))
    databases = cached_data.get('databases', [])
    backups = {}
    backup_client = get_mongo_backup_exporter_client()
    if backup_client:
        backup_payload = backup_client.get_cluster_backups(cluster_name, databases)
        backups = {
            database: MongoDatabaseBackup(**payload)
            for database, payload in backup_payload.items()
        }

    # Users only if authenticated
    users = None
    if has_permission(current_user, "mongodb.users.read"):
        users_data = cached_data.get('users', [])
        users = [DatabaseUser(**user_data) for user_data in users_data]

    return Cluster(
        name=cached_data['name'],
        nodes=nodes,
        databases=databases,
        users=users,
        users_visible=has_permission(current_user, "mongodb.users.read"),
        feature_compatibility_version=cached_data.get('feature_compatibility_version'),
        backups=backups,
        last_update=datetime.fromisoformat(cached_data.get('last_update', datetime.utcnow().isoformat()))
    )


@router.get("/{cluster_name}/nodes", response_model=List[Node])
async def get_cluster_nodes(cluster_name: str):
    """Get nodes for a cluster"""
    cluster = await get_cluster(cluster_name)
    return cluster.nodes


@router.post(
    "/{cluster_name}/stepdown",
    response_model=MongoStepDownResponse,
)
async def step_down_primary(
    cluster_name: str,
    payload: MongoStepDownRequest,
    request: Request,
    current_user: dict = Depends(require_permission("mongodb.stepdown")),
):
    """Step down the current MongoDB PRIMARY for a configured replica set."""
    try:
        result = collector.step_down_primary(cluster_name, payload.stepdown_seconds)
    except ValueError as exc:
        message = str(exc)
        if "not found" in message.lower():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=message)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)
    except Exception as exc:
        logger.error("MongoDB stepdown failed: cluster=%s error_type=%s", cluster_name, type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Не удалось снять PRIMARY. Проверьте состояние replica set и права мониторинговой учетной записи.",
        )

    task = celery_app.send_task(
        "refresh_target",
        args=["mongodb", cluster_name],
        queue=MONITOR_SPECS["mongodb"]["queue"],
    )
    audit_log(
        "mongodb_stepdown",
        user=current_user,
        request=request,
        permission="mongodb.stepdown",
        target_type="mongodb",
        target_name=cluster_name,
        previous_primary=result["previous_primary"],
        stepdown_seconds=result["stepdown_seconds"],
        refresh_task_id=task.id,
    )
    return MongoStepDownResponse(
        status="queued_refresh",
        cluster=cluster_name,
        previous_primary=result["previous_primary"],
        stepdown_seconds=result["stepdown_seconds"],
        refresh_task_id=task.id,
    )


@router.get("/{cluster_name}/databases", response_model=List[str])
async def get_cluster_databases(
    cluster_name: str,
    current_user: Optional[dict] = Depends(get_optional_user)
):
    """Get databases for a cluster"""
    cluster = await get_cluster(cluster_name, current_user)
    return cluster.databases


@router.get("/{cluster_name}/databases/{db_name}/collections", response_model=List[Collection])
async def get_database_collections(cluster_name: str, db_name: str):
    """Get collections for a database"""
    # Try cache first
    cache_key = f"collections:{cluster_name}:{db_name}"
    cached = cache.redis_client.get(cache_key)
    if cached:
        import json
        collections_data = json.loads(cached)
        return [Collection(**col) for col in collections_data]

    # Collect from MongoDB
    collections_data = collector.collect_collections(cluster_name, db_name)
    if collections_data:
        import json
        cache.redis_client.setex(cache_key, 300, json.dumps(collections_data))  # Cache for 5 minutes
    return [Collection(**col) for col in collections_data]


@router.get(
    "/{cluster_name}/databases/{db_name}/collections/{collection_name}/indexes",
    response_model=List[CollectionIndex]
)
async def get_collection_indexes(cluster_name: str, db_name: str, collection_name: str):
    """Get indexes for a specific collection"""
    cache_key = f"indexes:{cluster_name}:{db_name}:{collection_name}"
    cached = cache.redis_client.get(cache_key)
    if cached:
        import json
        indexes_data = json.loads(cached)
        log_collector_progress(
            logger,
            "mongodb",
            cluster_name,
            "indexes_cache_hit",
            database=db_name,
            collection=collection_name,
            count=len(indexes_data),
        )
        return [CollectionIndex(**idx) for idx in indexes_data]

    log_monitor_request(
        logger,
        "mongodb",
        "listIndexes",
        host=cluster_name,
        database=db_name,
        collection=collection_name,
    )
    indexes_data = collector.collect_collection_indexes(cluster_name, db_name, collection_name)
    import json
    # Cache index metadata (including empty results) for 10 minutes.
    cache.redis_client.setex(cache_key, 600, json.dumps(indexes_data))
    log_collector_progress(
        logger,
        "mongodb",
        cluster_name,
        "indexes_cache_success",
        database=db_name,
        collection=collection_name,
        count=len(indexes_data),
        ttl_s=600,
    )
    return [CollectionIndex(**idx) for idx in indexes_data]


@router.get("/{cluster_name}/users", response_model=List[DatabaseUser])
async def get_cluster_users(
    cluster_name: str,
    current_user: dict = Depends(require_permission("mongodb.users.read"))
):
    """Get users for a cluster (requires authentication)"""
    cluster = await get_cluster(cluster_name, current_user)
    if not cluster.users:
        return []
    return cluster.users
