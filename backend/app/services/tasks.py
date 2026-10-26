"""
Celery tasks for collecting MongoDB data
"""
import logging
import json
import uuid
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterator, List, Optional, Tuple
from app.services.celery_app import celery_app
from app.services.monitor_runtime import collect_target_batch, cache_target
from app.services.postgresql_collector import PostgresqlCollector
from app.services.kafka_collector import KafkaCollector
from app.services.mongo_collector import MongoCollector
from app.services.rabbitmq_collector import RabbitMQCollector
from app.services.redis_collector import RedisCollector
from app.services.haproxy_collector import HAProxyCollector
from app.services.redpanda_collector import RedpandaCollector
from app.services.elasticsearch_collector import ElasticsearchCollector
from app.services.emqx_collector import EMQXCollector
from app.services.cache import CacheService
from app.monitor_logging import log_collector_progress, log_collector_summary
from app.config import get_app_config, settings

logger = logging.getLogger(__name__)

_mongo_collector: Optional[MongoCollector] = None
_mongo_cache: Optional[CacheService] = None
_system_cache: Optional[CacheService] = None
_rabbitmq_collector: Optional[RabbitMQCollector] = None
_rabbitmq_cache: Optional[CacheService] = None
_redis_collector: Optional[RedisCollector] = None
_redis_cache: Optional[CacheService] = None
_haproxy_collector: Optional[HAProxyCollector] = None
_haproxy_cache: Optional[CacheService] = None
_redpanda_collector: Optional[RedpandaCollector] = None
_elasticsearch_collector: Optional[ElasticsearchCollector] = None
_emqx_collector: Optional[EMQXCollector] = None
_logging_cache: Optional[CacheService] = None
_emqx_cache: Optional[CacheService] = None
_argo_rollout_cache: Optional[CacheService] = None
_argo_rollout_collector = None


def _get_mongo_collector() -> MongoCollector:
    global _mongo_collector
    if _mongo_collector is None:
        _mongo_collector = MongoCollector()
    return _mongo_collector


def _get_mongo_cache() -> CacheService:
    global _mongo_cache
    if _mongo_cache is None:
        _mongo_cache = CacheService(
            db=settings.redis_mongodb_db
            if settings.redis_mongodb_db is not None
            else (settings.redis_cache_db if settings.redis_cache_db is not None else settings.redis_db)
        )
    return _mongo_cache


def _get_system_cache() -> CacheService:
    global _system_cache
    if _system_cache is None:
        _system_cache = CacheService(
            db=settings.redis_system_db
            if settings.redis_system_db is not None
            else (settings.redis_cache_db if settings.redis_cache_db is not None else settings.redis_db)
        )
    return _system_cache


def _get_rabbitmq_collector() -> RabbitMQCollector:
    global _rabbitmq_collector
    if _rabbitmq_collector is None:
        _rabbitmq_collector = RabbitMQCollector()
    return _rabbitmq_collector


def _get_rabbitmq_cache() -> CacheService:
    global _rabbitmq_cache
    if _rabbitmq_cache is None:
        _rabbitmq_cache = CacheService(
            db=settings.redis_rabbitmq_db if settings.redis_rabbitmq_db is not None else settings.redis_db
        )
    return _rabbitmq_cache


def _get_redis_collector() -> RedisCollector:
    global _redis_collector
    if _redis_collector is None:
        _redis_collector = RedisCollector()
    return _redis_collector


def _get_redis_cache() -> CacheService:
    global _redis_cache
    if _redis_cache is None:
        _redis_cache = CacheService(
            db=(
                settings.redis_redis_db
                if settings.redis_redis_db is not None
                else (
                    settings.redis_cache_db
                    if settings.redis_cache_db is not None
                    else settings.redis_db
                )
            )
        )
    return _redis_cache


def _get_haproxy_collector() -> HAProxyCollector:
    global _haproxy_collector
    if _haproxy_collector is None:
        _haproxy_collector = HAProxyCollector()
    return _haproxy_collector


def _get_haproxy_cache() -> CacheService:
    global _haproxy_cache
    if _haproxy_cache is None:
        _haproxy_cache = CacheService(
            db=settings.redis_haproxy_db if settings.redis_haproxy_db is not None else settings.redis_db
        )
    return _haproxy_cache


def _get_redpanda_collector() -> RedpandaCollector:
    global _redpanda_collector
    if _redpanda_collector is None:
        _redpanda_collector = RedpandaCollector()
    return _redpanda_collector


def _get_elasticsearch_collector() -> ElasticsearchCollector:
    global _elasticsearch_collector
    if _elasticsearch_collector is None:
        _elasticsearch_collector = ElasticsearchCollector()
    return _elasticsearch_collector


def _get_emqx_collector() -> EMQXCollector:
    global _emqx_collector
    if _emqx_collector is None:
        _emqx_collector = EMQXCollector()
    return _emqx_collector


def _get_logging_cache() -> CacheService:
    global _logging_cache
    if _logging_cache is None:
        _logging_cache = CacheService(
            db=settings.redis_logging_db if settings.redis_logging_db is not None else settings.redis_db
        )
    return _logging_cache


def _get_emqx_cache() -> CacheService:
    global _emqx_cache
    if _emqx_cache is None:
        _emqx_cache = CacheService(
            db=settings.redis_emqx_db if settings.redis_emqx_db is not None else settings.redis_db
        )
    return _emqx_cache


def _get_argo_rollout_cache() -> CacheService:
    global _argo_rollout_cache
    if _argo_rollout_cache is None:
        _argo_rollout_cache = CacheService(
            db=settings.redis_rollouts_db if settings.redis_rollouts_db is not None else settings.redis_db
        )
    return _argo_rollout_cache


def _get_argo_rollout_collector():
    global _argo_rollout_collector
    if _argo_rollout_collector is None:
        from app.services.argo_rollout_collector import get_argo_rollout_collector
        _argo_rollout_collector = get_argo_rollout_collector()
    return _argo_rollout_collector


def _collect_all_addresses_sync() -> List[str]:
    from app.services.registry_collector import RegistryCollector

    log_collector_progress(logger, "addresses", "addresses", "collect_start")
    result = RegistryCollector().collect_registry()
    if getattr(result, "status", "") == "busy":
        log_collector_progress(logger, "addresses", "addresses", "lock_skip")
        return []
    log_collector_progress(logger, "addresses", "addresses", "cache_success")
    _queue_target_update_notification("addresses:addresses")
    log_collector_summary(logger, "addresses", "updated", targets=["addresses"])
    return ["addresses"]

RABBITMQ_CACHE_TTL_S = 60
REDIS_CACHE_TTL_S = 60
HAPROXY_CACHE_TTL_S = 60
LOGGING_CACHE_TTL_S = 60
EMQX_CACHE_TTL_S = 60
ARGO_ROLLOUT_CACHE_TTL_S = 120  # Extended TTL to cover collection interval gaps
COLLECTOR_LOCK_TTL_S = max(360, int(settings.update_interval) * 3)


@contextmanager
def _collector_lock(lock_name: str) -> Iterator[bool]:
    """Distributed lock to prevent overlapping collector runs across workers."""
    lock_key = f"lock:collector:{lock_name}"
    lock_token = str(uuid.uuid4())
    acquired = False
    try:
        acquired = bool(
            _get_system_cache().redis_client.set(
                lock_key,
                lock_token,
                ex=COLLECTOR_LOCK_TTL_S,
                nx=True,
            )
        )
        if not acquired:
            log_collector_progress(logger, "collector", lock_name, "lock_skip")
        yield acquired
    finally:
        if acquired:
            try:
                # Use Lua script for atomic lock release (prevents race condition)
                release_script = """
                if redis.call("get", KEYS[1]) == ARGV[1] then
                    return redis.call("del", KEYS[1])
                else
                    return 0
                end
                """
                _get_system_cache().redis_client.eval(release_script, 1, lock_key, lock_token)
            except Exception as e:
                logger.warning("Could not release collector lock %s: %s", lock_name, e)

def _queue_update_notifications(updated_ids: List[str]) -> None:
    if not updated_ids:
        return

    try:
        redis_client = _get_system_cache().redis_client
        merge_script = """
        local existing = redis.call("GET", KEYS[1])
        local values = {}
        if existing then
            for token in string.gmatch(existing, "([^,]+)") do
                values[token] = true
            end
        end
        for i = 1, #ARGV - 1 do
            if ARGV[i] ~= "" then
                values[ARGV[i]] = true
            end
        end
        local merged = {}
        for token, _ in pairs(values) do
            table.insert(merged, token)
        end
        table.sort(merged)
        local value = table.concat(merged, ",")
        redis.call("SETEX", KEYS[1], tonumber(ARGV[#ARGV]), value)
        return value
        """
        merged = redis_client.eval(merge_script, 1, "websocket:notify", *updated_ids, 5)
        log_collector_progress(logger, "websocket", "websocket:notify", "notification_queued", targets=str(merged))
    except Exception as exc:
        logger.warning("Could not queue WebSocket notification for %s: %s", updated_ids, exc)


def _queue_target_update_notification(target_id: str) -> None:
    _queue_update_notifications([target_id])


@celery_app.task(name='collect_cluster_data')
def collect_cluster_data_task(cluster_name: str) -> Optional[dict]:
    """Task to collect data for a single cluster"""
    lock_name = f"mongodb:cluster:{cluster_name}"
    with _collector_lock(lock_name) as lock_acquired:
        if not lock_acquired:
            return None
        try:
            log_collector_progress(logger, "mongodb", cluster_name, "collect_start")
            data = _get_mongo_collector().collect_cluster_data(cluster_name)

            if data:
                _get_mongo_cache().set_cluster_data(cluster_name, data)
                log_collector_progress(logger, "mongodb", cluster_name, "cache_success")
                return data
            else:
                logger.warning(f"No data collected for cluster: {cluster_name}")
                return None
        except Exception as e:
            logger.error(f"Error in collect_cluster_data_task for {cluster_name}: {e}")
            return None


def _collect_one_cluster(cluster_name: str) -> Tuple[str, Optional[dict]]:
    """Collect data for one cluster under per-cluster lock (for parallel execution)."""
    with _collector_lock(f"mongodb:cluster:{cluster_name}") as lock_acquired:
        if not lock_acquired:
            return (cluster_name, None)
        try:
            log_collector_progress(logger, "mongodb", cluster_name, "collect_start")
            data = _get_mongo_collector().collect_cluster_data(cluster_name)
            return (cluster_name, data)
        except Exception as e:
            logger.error("Error collecting data for cluster %s: %s", cluster_name, e)
            return (cluster_name, None)


def _collect_all_clusters_sync() -> List[str]:
    """Synchronous function to collect data for all clusters. Uses per-cluster lock so one slow cluster does not block others."""
    from app.config import get_app_config
    current_config = get_app_config()
    if not current_config:
        logger.error("App config not initialized")
        return []

    cluster_names = [c.name for c in current_config.clusters]
    updated_clusters: List[str] = []
    max_workers = min(len(cluster_names) or 1, 8)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_collect_one_cluster, name): name for name in cluster_names}
        for future in as_completed(futures):
            name, data = future.result()
            if data:
                _get_mongo_cache().set_cluster_data(name, data)
                log_collector_progress(logger, "mongodb", name, "cache_success")
                updated_clusters.append(name)
            else:
                logger.warning("No data collected for cluster: %s", name)

    if current_config:
        total_clusters = len(current_config.clusters)
        total_nodes = sum(len(c.nodes) for c in current_config.clusters)
        _get_system_cache().set_stats({
            'total_clusters': total_clusters,
            'total_nodes': total_nodes
        })

    # Keep plain Mongo cluster names for backward-compatible websocket consumers.
    _queue_update_notifications(updated_clusters)

    log_collector_summary(logger, "mongodb", "updated", targets=updated_clusters)
    return updated_clusters


@celery_app.task(name='collect_all_clusters')
def collect_all_clusters_task() -> List[str]:
    """Task to collect data for all clusters.

    Runs collection synchronously inside the task to avoid nested Celery calls.
    """
    return _collect_all_clusters_sync()


def _collect_one_rabbitmq(target_name: str) -> Tuple[str, Optional[dict]]:
    """Collect one RabbitMQ target under per-target lock."""
    with _collector_lock(f"rabbitmq:target:{target_name}") as lock_acquired:
        if not lock_acquired:
            return (target_name, None)
        try:
            log_collector_progress(logger, "rabbitmq", target_name, "collect_start")
            data = _get_rabbitmq_collector().collect_target_data(target_name)
            return (target_name, data)
        except Exception as e:
            logger.error("Error collecting RabbitMQ data for target %s: %s", target_name, e)
            return (target_name, None)


def _collect_all_rabbitmq_sync() -> List[str]:
    config = get_app_config()
    return collect_target_batch(
        config.rabbitmq_targets if config else [], _collect_one_rabbitmq,
        _cache_one_rabbitmq, _queue_update_notifications, "rabbitmq",
    )


@celery_app.task(name='collect_all_rabbitmq')
def collect_all_rabbitmq_task() -> List[str]:
    """Task to collect data for all RabbitMQ targets."""
    return _collect_all_rabbitmq_sync()


def _collect_one_redis(target_name: str) -> Tuple[str, Optional[dict]]:
    """Collect one Redis target under per-target lock."""
    with _collector_lock(f"redis:target:{target_name}") as lock_acquired:
        if not lock_acquired:
            return (target_name, None)
        try:
            log_collector_progress(logger, "redis", target_name, "collect_start")
            data = _get_redis_collector().collect_target_data(target_name)
            return (target_name, data)
        except Exception as e:
            logger.error("Error collecting Redis data for target %s: %s", target_name, e)
            return (target_name, None)


def _collect_all_redis_sync() -> List[str]:
    config = get_app_config()
    return collect_target_batch(
        config.redis_targets if config else [], _collect_one_redis,
        _cache_one_redis, _queue_update_notifications, "redis",
    )


@celery_app.task(name='collect_all_redis')
def collect_all_redis_task() -> List[str]:
    """Task to collect data for all Redis targets."""
    return _collect_all_redis_sync()


def _collect_one_haproxy(target_name: str) -> Tuple[str, Optional[dict]]:
    """Collect one HAProxy target under per-target lock."""
    with _collector_lock(f"haproxy:target:{target_name}") as lock_acquired:
        if not lock_acquired:
            return (target_name, None)
        try:
            log_collector_progress(logger, "haproxy", target_name, "collect_start")
            data = _get_haproxy_collector().collect_target_data(target_name)
            return (target_name, data)
        except Exception as e:
            logger.error("Error collecting HAProxy data for target %s: %s", target_name, e)
            return (target_name, None)


def _collect_all_haproxy_sync() -> List[str]:
    config = get_app_config()
    return collect_target_batch(
        config.haproxy_targets if config else [], _collect_one_haproxy,
        _cache_one_haproxy, _queue_update_notifications, "haproxy",
    )


@celery_app.task(name='collect_all_haproxy')
def collect_all_haproxy_task() -> List[str]:
    """Task to collect data for all HAProxy targets."""
    return _collect_all_haproxy_sync()


def _collect_one_redpanda(target_name: str) -> Tuple[str, Optional[dict]]:
    """Collect one Redpanda target under per-target lock."""
    with _collector_lock(f"redpanda:target:{target_name}") as lock_acquired:
        if not lock_acquired:
            return (target_name, None)
        try:
            log_collector_progress(logger, "redpanda", target_name, "collect_start")
            data = _get_redpanda_collector().collect_target_data(target_name)
            return (target_name, data)
        except Exception as e:
            logger.error("Error collecting Redpanda data for target %s: %s", target_name, e)
            return (target_name, None)


def _collect_all_redpanda_sync() -> List[str]:
    config = get_app_config()
    return collect_target_batch(
        config.redpanda_targets if config else [], _collect_one_redpanda,
        _cache_one_redpanda, _queue_update_notifications, "redpanda",
    )


@celery_app.task(name='collect_all_redpanda')
def collect_all_redpanda_task() -> List[str]:
    """Task to collect data for all Redpanda targets."""
    return _collect_all_redpanda_sync()


def _collect_one_elasticsearch(target_name: str) -> Tuple[str, Optional[dict]]:
    """Collect one Elasticsearch target under per-target lock."""
    with _collector_lock(f"elasticsearch:target:{target_name}") as lock_acquired:
        if not lock_acquired:
            return (target_name, None)
        try:
            log_collector_progress(logger, "elasticsearch", target_name, "collect_start")
            data = _get_elasticsearch_collector().collect_target_data(target_name)
            return (target_name, data)
        except Exception as e:
            logger.error("Error collecting Elasticsearch data for target %s: %s", target_name, e)
            return (target_name, None)


def _collect_all_elasticsearch_sync() -> List[str]:
    config = get_app_config()
    return collect_target_batch(
        config.elasticsearch_targets if config else [], _collect_one_elasticsearch,
        _cache_one_elasticsearch, _queue_update_notifications, "elasticsearch",
    )


@celery_app.task(name='collect_all_elasticsearch')
def collect_all_elasticsearch_task() -> List[str]:
    """Task to collect data for all Elasticsearch targets."""
    return _collect_all_elasticsearch_sync()


def _collect_one_emqx(target_name: str) -> Tuple[str, Optional[dict]]:
    """Collect one EMQX target under per-target lock."""
    with _collector_lock(f"emqx:target:{target_name}") as lock_acquired:
        if not lock_acquired:
            return (target_name, None)
        try:
            log_collector_progress(logger, "emqx", target_name, "collect_start")
            data = _get_emqx_collector().collect_target_data(target_name)
            return (target_name, data)
        except Exception as e:
            logger.error("Error collecting EMQX data for target %s: %s", target_name, e)
            return (target_name, None)


def _collect_all_emqx_sync() -> List[str]:
    config = get_app_config()
    return collect_target_batch(
        config.emqx_targets if config else [], _collect_one_emqx,
        _cache_one_emqx, _queue_update_notifications, "emqx",
    )


@celery_app.task(name='collect_all_emqx')
def collect_all_emqx_task() -> List[str]:
    """Task to collect data for all EMQX targets."""
    return _collect_all_emqx_sync()


def _cache_one_rabbitmq(target_name: str, data: dict) -> None:
    _get_rabbitmq_cache().redis_client.setex(
        f"rabbitmq:target:{target_name}",
        RABBITMQ_CACHE_TTL_S,
        json.dumps(data, default=str),
    )


def _cache_one_redis(target_name: str, data: dict) -> None:
    _get_redis_cache().redis_client.setex(
        f"redis:target:{target_name}",
        REDIS_CACHE_TTL_S,
        json.dumps(data, default=str),
    )


def _cache_one_haproxy(target_name: str, data: dict) -> None:
    cache_key = f"haproxy:target:{target_name}"
    existing = _get_haproxy_cache().redis_client.get(cache_key)
    if existing:
        try:
            existing_data = json.loads(existing)
            if existing_data.get("k8s_pods"):
                data["k8s_pods"] = existing_data["k8s_pods"]
        except (json.JSONDecodeError, TypeError):
            pass
    _get_haproxy_cache().redis_client.setex(
        cache_key,
        HAPROXY_CACHE_TTL_S,
        json.dumps(data, default=str),
    )


def _cache_one_redpanda(target_name: str, data: dict) -> None:
    _get_logging_cache().redis_client.setex(
        f"redpanda:target:{target_name}",
        LOGGING_CACHE_TTL_S,
        json.dumps(data, default=str),
    )


def _cache_one_elasticsearch(target_name: str, data: dict) -> None:
    _get_logging_cache().redis_client.setex(
        f"elasticsearch:target:{target_name}",
        LOGGING_CACHE_TTL_S,
        json.dumps(data, default=str),
    )


def _cache_one_emqx(target_name: str, data: dict) -> None:
    _get_emqx_cache().redis_client.setex(
        f"emqx:target:{target_name}",
        EMQX_CACHE_TTL_S,
        json.dumps(data, default=str),
    )


def _resolve_argo_rollout_namespace(target_name: str) -> Optional[str]:
    current_config = get_app_config()
    if not current_config:
        return None
    for monitor in getattr(current_config, 'monitors', []):
        if monitor.get('name') != 'rollouts':
            continue
        monitor_config = monitor.get('config', {})
        targets = monitor_config.get('targets', [])
        if isinstance(targets, list):
            for target in targets:
                if isinstance(target, dict) and target.get('name') == target_name:
                    return target.get('namespace', 'default')
        if target_name == 'argo-rollout':
            return monitor_config.get('namespace', 'default')
    return None


@celery_app.task(name='refresh_target')
def refresh_target_task(target_type: str, target_name: str) -> dict:
    """Refresh one configured monitor target.

    The task reuses existing collector locks; duplicate admin clicks enqueue tasks,
    but only one collector run per target can execute at a time.
    """
    normalized_type = "addresses" if target_type == "registry" else target_type
    target_id = f"{normalized_type}:{target_name}"
    try:
        if normalized_type == "mongodb":
            _, data = _collect_one_cluster(target_name)
            if data:
                _get_mongo_cache().set_cluster_data(target_name, data)
                log_collector_progress(logger, "mongodb", target_name, "cache_success", mode="manual_refresh")
                _queue_target_update_notification(target_id)
                log_collector_summary(logger, "mongodb", "manual_refresh_updated", targets=[target_name])
                return {"status": "updated", "target_id": target_id}
            return {"status": "skipped", "target_id": target_id}

        if normalized_type == "rabbitmq":
            _, data = _collect_one_rabbitmq(target_name)
            if data:
                _cache_one_rabbitmq(target_name, data)
                log_collector_progress(logger, "rabbitmq", target_name, "cache_success", mode="manual_refresh")
                _queue_target_update_notification(target_id)
                log_collector_summary(logger, "rabbitmq", "manual_refresh_updated", targets=[target_name])
                return {"status": "updated", "target_id": target_id}
            return {"status": "skipped", "target_id": target_id}

        if normalized_type == "kafka":
            _, data = _collect_one_kafka(target_name)
            if data:
                cache_target("kafka", target_name, data)
                _queue_target_update_notification(target_id)
                return {"status":"updated", "target_id":target_id}
            return {"status":"skipped", "target_id":target_id}

        if normalized_type == "postgresql":
            _, data = _collect_one_postgresql(target_name)
            if data:
                _cache_one_postgresql(target_name, data)
                _queue_target_update_notification(target_id)
                return {"status": "updated", "target_id": target_id}
            return {"status": "skipped", "target_id": target_id}

        if normalized_type == "redis":
            _, data = _collect_one_redis(target_name)
            if data:
                _cache_one_redis(target_name, data)
                log_collector_progress(logger, "redis", target_name, "cache_success", mode="manual_refresh")
                _queue_target_update_notification(target_id)
                log_collector_summary(logger, "redis", "manual_refresh_updated", targets=[target_name])
                return {"status": "updated", "target_id": target_id}
            return {"status": "skipped", "target_id": target_id}

        if normalized_type == "haproxy":
            _, data = _collect_one_haproxy(target_name)
            if data:
                _cache_one_haproxy(target_name, data)
                log_collector_progress(logger, "haproxy", target_name, "cache_success", mode="manual_refresh")
                _queue_target_update_notification(target_id)
                log_collector_summary(logger, "haproxy", "manual_refresh_updated", targets=[target_name])
                return {"status": "updated", "target_id": target_id}
            return {"status": "skipped", "target_id": target_id}

        if normalized_type == "redpanda":
            _, data = _collect_one_redpanda(target_name)
            if data:
                _cache_one_redpanda(target_name, data)
                log_collector_progress(logger, "redpanda", target_name, "cache_success", mode="manual_refresh")
                _queue_target_update_notification(target_id)
                log_collector_summary(logger, "redpanda", "manual_refresh_updated", targets=[target_name])
                return {"status": "updated", "target_id": target_id}
            return {"status": "skipped", "target_id": target_id}

        if normalized_type == "elasticsearch":
            _, data = _collect_one_elasticsearch(target_name)
            if data:
                _cache_one_elasticsearch(target_name, data)
                log_collector_progress(logger, "elasticsearch", target_name, "cache_success", mode="manual_refresh")
                _queue_target_update_notification(target_id)
                log_collector_summary(logger, "elasticsearch", "manual_refresh_updated", targets=[target_name])
                return {"status": "updated", "target_id": target_id}
            return {"status": "skipped", "target_id": target_id}

        if normalized_type == "emqx":
            _, data = _collect_one_emqx(target_name)
            if data:
                _cache_one_emqx(target_name, data)
                log_collector_progress(logger, "emqx", target_name, "cache_success", mode="manual_refresh")
                _queue_target_update_notification(target_id)
                log_collector_summary(logger, "emqx", "manual_refresh_updated", targets=[target_name])
                return {"status": "updated", "target_id": target_id}
            return {"status": "skipped", "target_id": target_id}

        if normalized_type == "argo-rollout":
            namespace = _resolve_argo_rollout_namespace(target_name)
            if not namespace:
                return {"status": "not_found", "target_id": target_id}
            _, data = _collect_one_argo_rollout(namespace, target_name)
            if data:
                _get_argo_rollout_cache().redis_client.setex(
                    f"argo_rollout:target:{target_name}",
                    ARGO_ROLLOUT_CACHE_TTL_S,
                    json.dumps(data, default=str),
                )
                log_collector_progress(logger, "argo-rollout", target_name, "cache_success", mode="manual_refresh", namespace=namespace)
                _queue_target_update_notification(target_id)
                log_collector_summary(logger, "argo-rollout", "manual_refresh_updated", targets=[target_name], namespace=namespace)
                return {"status": "updated", "target_id": target_id}
            return {"status": "skipped", "target_id": target_id}

        if normalized_type == "addresses":
            updated = _collect_all_addresses_sync()
            if updated:
                _queue_target_update_notification(target_id)
                log_collector_summary(logger, "addresses", "manual_refresh_updated", targets=updated)
                return {"status": "updated", "target_id": target_id}
            return {"status": "skipped", "target_id": target_id}

        return {"status": "unsupported", "target_id": target_id}
    except Exception as exc:
        logger.error("Refresh target %s failed: %s", target_id, exc)
        return {"status": "error", "target_id": target_id, "error": str(exc)}


def _collect_one_argo_rollout(namespace: str, target_name: str) -> Tuple[str, Optional[dict]]:
    """Collect one Argo Rollout namespace under per-target lock."""
    with _collector_lock(f"argo_rollout:target:{target_name}") as lock_acquired:
        if not lock_acquired:
            return (target_name, None)
        try:
            log_collector_progress(logger, "argo-rollout", target_name, "collect_start", namespace=namespace)
            data = _get_argo_rollout_collector().collect_target_data(namespace)
            return (target_name, data)
        except Exception as e:
            logger.error("Error collecting Argo Rollout data for target %s: %s", target_name, e)
            return (target_name, None)


def _collect_all_argo_rollout_sync() -> List[str]:
    """Synchronous function to collect Argo Rollout data for all configured targets. Uses per-target lock."""
    from app.config import get_app_config

    current_config = get_app_config()
    if not current_config:
        logger.error("App config not initialized")
        return []

    updated_targets: List[str] = []

    # Get targets from rollouts monitor config
    targets_to_collect = []  # List of (target_name, namespace)
    logger.debug("Looking for rollouts monitor in %s monitors", len(getattr(current_config, 'monitors', [])))
    for monitor in getattr(current_config, 'monitors', []):
        monitor_name = monitor.get('name', '')
        logger.debug("Checking monitor: %s", monitor_name)
        # Monitor name is now 'rollouts' (not 'argo-rollout')
        if monitor_name == 'rollouts':
            monitor_config = monitor.get('config', {})
            # Check if there's a targets array
            targets = monitor_config.get('targets', [])
            logger.debug("Found rollouts monitor with %s targets", len(targets))
            if targets:
                for target in targets:
                    target_name = target.get('name', '')
                    namespace = target.get('namespace', 'default')
                    logger.debug("Adding target: %s (namespace: %s)", target_name, namespace)
                    if target_name:  # Only add if target has a name
                        targets_to_collect.append((target_name, namespace))
            else:
                # Fallback to direct namespace in config
                target_name = 'argo-rollout'
                namespace = monitor_config.get('namespace', 'default')
                targets_to_collect.append((target_name, namespace))

    logger.debug("Total targets to collect: %s", len(targets_to_collect))
    if not targets_to_collect:
        log_collector_progress(logger, "argo-rollout", "argo-rollout", "no_targets")
        return []

    max_workers = min(len(targets_to_collect) or 1, 4)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_collect_one_argo_rollout, namespace, target_name): (target_name, namespace) for target_name, namespace in targets_to_collect}
        for future in as_completed(futures):
            target_name, namespace = futures[future]
            data = future.result()[1]  # Get data from tuple (target_name, data)
            if data:
                # Save with target_name as key (not namespace)
                _get_argo_rollout_cache().redis_client.setex(
                    f"argo_rollout:target:{target_name}",
                    ARGO_ROLLOUT_CACHE_TTL_S,
                    json.dumps(data, default=str),
                )
                updated_targets.append(target_name)
                log_collector_progress(logger, "argo-rollout", target_name, "cache_success", namespace=namespace)
            else:
                logger.warning("No Argo Rollout data collected for target: %s (namespace: %s)", target_name, namespace)

    _queue_update_notifications([f"argo-rollout:{target_name}" for target_name in updated_targets])
    log_collector_summary(logger, "argo-rollout", "updated", targets=updated_targets)
    return updated_targets


@celery_app.task(name='collect_all_argo_rollout')
def collect_all_argo_rollout_task() -> List[str]:
    """Task to collect data for all Argo Rollout namespaces."""
    return _collect_all_argo_rollout_sync()


@celery_app.task(name='collect_all_addresses')
def collect_all_addresses_task() -> List[str]:
    """Task to collect address data from GitLab-backed sources."""
    return _collect_all_addresses_sync()


@celery_app.task(name='collect_all_registry')
def collect_all_registry_task() -> List[str]:
    """Backward-compatible alias for address collection."""
    return _collect_all_addresses_sync()


def _run_one_collector(fn, name: str) -> Tuple[str, List[str]]:
    """Run one collector sync; return (name, updated_ids)."""
    try:
        result = fn()
        return (name, result if isinstance(result, list) else [])
    except Exception as e:
        logger.error("Collector %s failed: %s", name, e)
        return (name, [])


@celery_app.task(name='collect_all_targets')
def collect_all_targets_task() -> dict:
    """Run all collector types in parallel. Total cycle time ≈ max(per-type time) instead of sum.
    Reduces load on targets by finishing the full cycle faster and reusing connections where cached."""
    import time as _time
    collector_specs = [
        (_collect_all_clusters_sync, "mongodb"),
        (_collect_all_rabbitmq_sync, "rabbitmq"),
        (_collect_all_redis_sync, "redis"),
        (_collect_all_haproxy_sync, "haproxy"),
        (_collect_all_redpanda_sync, "redpanda"),
        (_collect_all_elasticsearch_sync, "elasticsearch"),
        (_collect_all_emqx_sync, "emqx"),
        (_collect_all_argo_rollout_sync, "argo-rollout"),
        (_collect_all_addresses_sync, "addresses"),
    ]
    current_config = get_app_config()
    enabled_monitor_types = set(current_config.get_enabled_monitor_types()) if current_config else set()
    collectors = [
        (fn, name)
        for fn, name in collector_specs
        if name in enabled_monitor_types
    ]
    if not collectors:
        log_collector_progress(logger, "all", "all", "no_enabled_monitor_types")
        return {}
    t0 = _time.perf_counter()
    results: dict = {}
    with ThreadPoolExecutor(max_workers=len(collectors)) as executor:
        futures = [executor.submit(_run_one_collector, fn, name) for fn, name in collectors]
        for future in as_completed(futures):
            name, updated = future.result()
            results[name] = updated
    elapsed = _time.perf_counter() - t0
    log_collector_summary(
        logger,
        "all",
        "collect_all_finished",
        count=sum(len(v) for v in results.values()),
        elapsed_s=round(elapsed, 2),
        result_counts={k: len(v) for k, v in results.items()},
    )
    return results


def _collect_one_postgresql(target_name: str) -> Tuple[str, Optional[dict]]:
    with _collector_lock(f"postgresql:target:{target_name}") as acquired:
        if not acquired:
            return target_name, None
        return target_name, PostgresqlCollector().collect_target_data(target_name)


def _cache_one_postgresql(name: str, data: dict) -> None:
    cache_target("postgresql", name, data)


def _collect_all_postgresql_sync() -> List[str]:
    config = get_app_config()
    return collect_target_batch(
        config.postgresql_targets if config else [], _collect_one_postgresql,
        _cache_one_postgresql, _queue_update_notifications, "postgresql",
    )


@celery_app.task(name="collect_all_postgresql")
def collect_all_postgresql_task() -> List[str]:
    return _collect_all_postgresql_sync()


def _collect_one_kafka(name):
    with _collector_lock(f"kafka:target:{name}") as acquired:
        return (name,KafkaCollector().collect_target_data(name)) if acquired else (name,None)


def _collect_all_kafka_sync():
    config=get_app_config()
    return collect_target_batch(config.kafka_targets if config else [],_collect_one_kafka,
        lambda name,data:cache_target("kafka",name,data),_queue_update_notifications,"kafka")


@celery_app.task(name="collect_all_kafka")
def collect_all_kafka_task():
    return _collect_all_kafka_sync()
