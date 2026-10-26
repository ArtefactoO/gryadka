"""
Redis cache service for storing cluster data
"""
import json
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone
from redis.exceptions import RedisError
from app.config import settings
from app.services.redis_runtime import create_internal_redis_client

LAG_HISTORY_MAX_POINTS = 40
LAG_HISTORY_KEY_PREFIX = "lag_history"
DATABASES_OVERVIEW_KEY = "clusters:databases_overview"
logger = logging.getLogger(__name__)


class CacheService:
    """Service for caching cluster data in Redis"""

    def __init__(self, db: Optional[int] = None):
        target_db = db
        if target_db is None:
            if settings.redis_mongodb_db is not None:
                target_db = settings.redis_mongodb_db
            elif settings.redis_cache_db is not None:
                # Backward compatibility for previous env var name.
                target_db = settings.redis_cache_db
            else:
                target_db = settings.redis_db
        self.redis_client = create_internal_redis_client(db=target_db, decode_responses=True)

    def _get_key(self, cluster_name: Optional[str] = None) -> str:
        """Generate Redis key"""
        if cluster_name:
            return f"cluster:{cluster_name}"
        return "clusters:list"

    def _lag_history_key(self, cluster_name: str, node_name: str) -> str:
        return f"{LAG_HISTORY_KEY_PREFIX}:{cluster_name}:{node_name}"

    def get_raw(self, key: str) -> Optional[str]:
        try:
            return self.redis_client.get(key)
        except RedisError as exc:
            logger.warning("Redis read failed for key %s: %s", key, exc)
            return None

    def get_json(self, key: str) -> Optional[Any]:
        raw = self.get_raw(key)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning("Redis JSON decode failed for key %s: %s", key, exc)
            return None

    def set_json(self, key: str, value: Any, ttl_s: int) -> None:
        try:
            self.redis_client.setex(key, max(1, int(ttl_s)), json.dumps(value, default=str))
        except (RedisError, TypeError, ValueError) as exc:
            logger.warning("Redis JSON write failed for key %s: %s", key, exc)

    def push_lag_points(self, cluster_name: str, nodes: List[Dict[str, Any]], timestamp: Optional[str] = None) -> None:
        """Append one lag point per node to Redis list (for Lag History chart)."""
        ts = timestamp or datetime.now(timezone.utc).isoformat()
        for node in nodes:
            name = node.get("name")
            lag = node.get("lag", 0)
            if not name:
                continue
            key = self._lag_history_key(cluster_name, name)
            point = json.dumps({"time": ts, "lag": lag})
            self.redis_client.rpush(key, point)
            self.redis_client.ltrim(key, -LAG_HISTORY_MAX_POINTS, -1)
            self.redis_client.expire(key, 86400)  # 24h TTL so keys don't leak

    def get_lag_history(self, cluster_name: str) -> Dict[str, List[Dict[str, Any]]]:
        """Get lag history for all nodes of a cluster. Returns {node_name: [{time, lag}, ...]} oldest first.
        We keep last LAG_HISTORY_MAX_POINTS per node; when new points are pushed, ltrim drops the oldest,
        so the initial (oldest) point shifts forward in time with each collection."""
        pattern = f"{LAG_HISTORY_KEY_PREFIX}:{cluster_name}:*"
        try:
            keys = list(self.redis_client.scan_iter(match=pattern, count=200))
        except RedisError as exc:
            logger.warning("Redis read failed for lag history %s: %s", cluster_name, exc)
            return {}
        result: Dict[str, List[Dict[str, Any]]] = {}
        prefix_len = len(f"{LAG_HISTORY_KEY_PREFIX}:{cluster_name}:")
        for key in keys:
            node_name = key[prefix_len:]
            try:
                raw_list = self.redis_client.lrange(key, 0, -1) or []
            except RedisError as exc:
                logger.warning("Redis read failed for lag history key %s: %s", key, exc)
                continue
            points = []
            for item in raw_list:
                try:
                    points.append(json.loads(item))
                except (json.JSONDecodeError, TypeError):
                    continue
            # Ensure chronological order (oldest first) for chart
            points.sort(key=lambda p: (p.get("time") or ""))
            result[node_name] = points
        return result

    def set_cluster_data(self, cluster_name: str, data: Dict[str, Any]) -> None:
        """Store cluster data in cache"""
        key = self._get_key(cluster_name)
        data['last_update'] = datetime.now(timezone.utc).isoformat()
        self.redis_client.setex(
            key,
            3600,  # TTL 1 hour
            json.dumps(data, default=str)
        )
        self.push_lag_points(cluster_name, data.get("nodes", []), data.get("last_update"))
        self.update_databases_overview(cluster_name, data.get("databases", []))

    def update_databases_overview(self, cluster_name: str, databases: List[str]) -> None:
        """Update cached map of cluster -> databases for cross-cluster DB search."""
        raw = self.get_raw(DATABASES_OVERVIEW_KEY)
        overview: Dict[str, List[str]] = json.loads(raw) if raw else {}
        overview[cluster_name] = sorted(databases) if databases else []
        self.redis_client.setex(DATABASES_OVERVIEW_KEY, 3600, json.dumps(overview))

    def get_databases_overview(self) -> Dict[str, List[str]]:
        """Get cached map of cluster -> databases for MongoDB search."""
        raw = self.get_raw(DATABASES_OVERVIEW_KEY)
        if not raw:
            # Backfill overview from existing cluster cache on first request after deploy/restart.
            overview: Dict[str, List[str]] = {}
            for cluster_name in self.get_all_clusters():
                cached = self.get_cluster_data(cluster_name)
                if not cached:
                    continue
                dbs = cached.get("databases", [])
                overview[cluster_name] = sorted(dbs) if isinstance(dbs, list) else []
            if overview:
                self.redis_client.setex(DATABASES_OVERVIEW_KEY, 3600, json.dumps(overview))
            return overview
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}

    def get_cluster_data(self, cluster_name: str) -> Optional[Dict[str, Any]]:
        """Get cluster data from cache"""
        key = self._get_key(cluster_name)
        data = self.get_raw(key)
        if data:
            return json.loads(data)
        return None

    def get_all_clusters(self) -> List[str]:
        """Get list of all cached cluster names"""
        pattern = "cluster:*"
        try:
            keys = self.redis_client.keys(pattern)
        except RedisError as exc:
            logger.warning("Redis read failed for cluster list: %s", exc)
            return []
        return [key.replace("cluster:", "") for key in keys]

    def set_stats(self, stats: Dict[str, Any]) -> None:
        """Store overall statistics"""
        stats['last_update'] = datetime.now(timezone.utc).isoformat()
        self.redis_client.setex(
            "stats",
            3600,
            json.dumps(stats, default=str)
        )

    def get_stats(self) -> Optional[Dict[str, Any]]:
        """Get overall statistics"""
        data = self.get_raw("stats")
        if data:
            return json.loads(data)
        return None

    def clear_cache(self) -> None:
        """Clear all cached data, lag history, and databases overview"""
        pattern = "cluster:*"
        keys = self.redis_client.keys(pattern)
        if keys:
            self.redis_client.delete(*keys)
        self.redis_client.delete("stats")
        self.redis_client.delete(DATABASES_OVERVIEW_KEY)
        lag_keys = self.redis_client.keys(f"{LAG_HISTORY_KEY_PREFIX}:*")
        if lag_keys:
            self.redis_client.delete(*lag_keys)

    def ping(self) -> bool:
        """Check Redis connection"""
        try:
            return self.redis_client.ping()
        except Exception:
            return False
