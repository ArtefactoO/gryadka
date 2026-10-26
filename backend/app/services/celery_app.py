"""
Celery application configuration.

Workers can be dedicated per monitor type by consuming only the corresponding queue:
  celery -A app.services.celery_app worker -Q mongodb -n worker-mongodb@%%h
  celery -A app.services.celery_app worker -Q rabbitmq -n worker-rabbitmq@%%h
  ...
Or one worker can handle all queues: -Q mongodb,rabbitmq,redis,haproxy,redpanda,elasticsearch,emqx
"""
from urllib.parse import quote
from celery import Celery
from celery.signals import after_task_publish, worker_ready, beat_init, setup_logging
from kombu import Queue
from app.config import get_app_config, settings
from app.logging_setup import configure_logging
from app.services.redis_runtime import create_internal_redis_client
import logging
from redis.exceptions import RedisError

configure_logging(service="gryadka")
logger = logging.getLogger(__name__)


def _resolve_update_interval() -> int:
    """Resolve collector interval, preferring config.yaml over env fallback."""
    try:
        from app.config import get_app_config
        current_config = get_app_config()
        if current_config and isinstance(current_config.update_interval_s, int) and current_config.update_interval_s > 0:
            return current_config.update_interval_s
    except Exception as e:
        logger.warning("Could not load update interval from config.yaml: %s", e)
    return max(1, int(settings.update_interval))

celery_db = settings.redis_celery_db if settings.redis_celery_db is not None else settings.redis_db
collector_interval_s = _resolve_update_interval()
_celery_redis_client = None

# Queues per monitor type so you can run dedicated workers per group
CELERY_QUEUE_MONGODB = 'mongodb'
CELERY_QUEUE_RABBITMQ = 'rabbitmq'
CELERY_QUEUE_REDIS = 'redis'
CELERY_QUEUE_HAPROXY = 'haproxy'
CELERY_QUEUE_REDPANDA = 'redpanda'
CELERY_QUEUE_ELASTICSEARCH = 'elasticsearch'
CELERY_QUEUE_EMQX = 'emqx'
CELERY_QUEUE_ARGO_ROLLOUT = 'rollouts'
CELERY_QUEUE_ADDRESSES = 'addresses'

MONITOR_SPECS = {
    "mongodb": {
        "queue": CELERY_QUEUE_MONGODB,
        "task": "collect_all_clusters",
        "schedule_name": "collect-mongodb",
        "schedule_multiplier": 1,
    },
    "rabbitmq": {
        "queue": CELERY_QUEUE_RABBITMQ,
        "task": "collect_all_rabbitmq",
        "schedule_name": "collect-rabbitmq",
        "schedule_multiplier": 1,
    },
    "redis": {
        "queue": CELERY_QUEUE_REDIS,
        "task": "collect_all_redis",
        "schedule_name": "collect-redis",
        "schedule_multiplier": 1,
    },
    "kafka": {"queue":"kafka", "task":"collect_all_kafka", "schedule_name":"collect-kafka", "schedule_multiplier":2},
    "postgresql": {
        "queue": "postgresql", "task": "collect_all_postgresql",
        "schedule_name": "collect-postgresql", "schedule_multiplier": 1,
    },
    "haproxy": {
        "queue": CELERY_QUEUE_HAPROXY,
        "task": "collect_all_haproxy",
        "schedule_name": "collect-haproxy",
        "schedule_multiplier": 1,
    },
    "redpanda": {
        "queue": CELERY_QUEUE_REDPANDA,
        "task": "collect_all_redpanda",
        "schedule_name": "collect-redpanda",
        "schedule_multiplier": 1,
    },
    "elasticsearch": {
        "queue": CELERY_QUEUE_ELASTICSEARCH,
        "task": "collect_all_elasticsearch",
        "schedule_name": "collect-elasticsearch",
        "schedule_multiplier": 1,
    },
    "emqx": {
        "queue": CELERY_QUEUE_EMQX,
        "task": "collect_all_emqx",
        "schedule_name": "collect-emqx",
        "schedule_multiplier": 1,
    },
    "argo-rollout": {
        "queue": CELERY_QUEUE_ARGO_ROLLOUT,
        "task": "collect_all_argo_rollout",
        "schedule_name": "collect-argo-rollout",
        "schedule_multiplier": 2,
    },
    "addresses": {
        "queue": CELERY_QUEUE_ADDRESSES,
        "task": "collect_all_addresses",
        "schedule_name": "collect-addresses",
        "schedule_multiplier": 1,
    },
}


def _resolve_addresses_interval() -> int:
    try:
        current_config = get_app_config()
        if current_config:
            addresses_config = current_config.get_addresses_monitor_config()
            interval = int(addresses_config.get("refreshIntervalS", 1200))
            return max(60, interval)
    except Exception as exc:
        logger.warning("Could not resolve addresses interval from config: %s", exc)
    return 1200


def _resolve_enabled_monitor_types() -> list[str]:
    """Return configured monitor types for this process."""
    try:
        current_config = get_app_config()
        if current_config:
            enabled = [
                monitor_type
                for monitor_type in current_config.get_enabled_monitor_types()
                if monitor_type in MONITOR_SPECS
            ]
            if enabled:
                return enabled
            return []
    except Exception as exc:
        logger.warning("Could not resolve enabled monitor types from config: %s", exc)
    return []


def _build_task_queues(enabled_monitor_types: list[str]) -> tuple[Queue, ...]:
    return tuple(Queue(MONITOR_SPECS[monitor_type]["queue"]) for monitor_type in enabled_monitor_types)


def _build_beat_schedule(enabled_monitor_types: list[str]) -> dict:
    schedule = {}
    for monitor_type in enabled_monitor_types:
        spec = MONITOR_SPECS[monitor_type]
        interval = _resolve_addresses_interval() if monitor_type == "addresses" else collector_interval_s * spec["schedule_multiplier"]
        expires = max(interval * max(1, int(settings.celery_task_expires_multiplier)), 60)
        schedule[spec["schedule_name"]] = {
            "task": spec["task"],
            "schedule": interval,
            "options": {"queue": spec["queue"], "expires": expires},
        }
    return schedule


ENABLED_MONITOR_TYPES = _resolve_enabled_monitor_types()
ENABLED_QUEUES = [MONITOR_SPECS[monitor_type]["queue"] for monitor_type in ENABLED_MONITOR_TYPES]

redis_auth = f":{quote(settings.redis_password, safe='')}@" if settings.redis_password else ""

celery_app = Celery(
    'gryadka',
    broker=f'redis://{redis_auth}{settings.redis_host}:{settings.redis_port}/{celery_db}',
    backend=f'redis://{redis_auth}{settings.redis_host}:{settings.redis_port}/{celery_db}',
    include=['app.services.tasks'],
)

celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
    task_track_started=True,
    task_time_limit=300,  # 5 minutes
    task_soft_time_limit=240,  # 4 minutes
    broker_connection_retry_on_startup=True,
    broker_connection_retry=True,
    broker_connection_max_retries=None,
    broker_pool_limit=10,
    redis_socket_connect_timeout=float(settings.redis_socket_connect_timeout_s),
    redis_socket_timeout=float(settings.redis_socket_timeout_s),
    redis_socket_keepalive=True,
    redis_retry_on_timeout=bool(settings.redis_retry_on_timeout),
    broker_transport_options={
        "visibility_timeout": int(settings.celery_broker_visibility_timeout_s),
        "socket_connect_timeout": float(settings.redis_socket_connect_timeout_s),
        "socket_timeout": float(settings.redis_socket_timeout_s),
        "socket_keepalive": True,
        "health_check_interval": int(settings.redis_health_check_interval_s),
        "retry_on_timeout": bool(settings.redis_retry_on_timeout),
        "max_connections": int(settings.celery_broker_max_connections),
    },
    result_backend_transport_options={
        "visibility_timeout": int(settings.celery_broker_visibility_timeout_s),
        "socket_connect_timeout": float(settings.redis_socket_connect_timeout_s),
        "socket_timeout": float(settings.redis_socket_timeout_s),
        "socket_keepalive": True,
        "health_check_interval": int(settings.redis_health_check_interval_s),
        "retry_on_timeout": bool(settings.redis_retry_on_timeout),
        "max_connections": int(settings.celery_broker_max_connections),
    },
    worker_prefetch_multiplier=1,
    worker_proc_alive_timeout=20,
    worker_max_tasks_per_child=50,
    worker_hijack_root_logger=False,
    task_queues=_build_task_queues(ENABLED_MONITOR_TYPES),
    task_routes={
        'collect_cluster_data': {'queue': CELERY_QUEUE_MONGODB},
        'collect_all_clusters': {'queue': CELERY_QUEUE_MONGODB},
        'collect_all_rabbitmq': {'queue': CELERY_QUEUE_RABBITMQ},
        'collect_all_redis': {'queue': CELERY_QUEUE_REDIS},
        'collect_all_haproxy': {'queue': CELERY_QUEUE_HAPROXY},
        'collect_all_redpanda': {'queue': CELERY_QUEUE_REDPANDA},
        'collect_all_elasticsearch': {'queue': CELERY_QUEUE_ELASTICSEARCH},
        'collect_all_emqx': {'queue': CELERY_QUEUE_EMQX},
        'collect_all_argo_rollout': {'queue': CELERY_QUEUE_ARGO_ROLLOUT},
        'collect_all_registry': {'queue': CELERY_QUEUE_ADDRESSES},
        'collect_all_addresses': {'queue': CELERY_QUEUE_ADDRESSES},
        'collect_all_targets': {'queue': CELERY_QUEUE_MONGODB},  # fallback when using single combined task
    },
    beat_schedule=_build_beat_schedule(ENABLED_MONITOR_TYPES),
)

ALL_QUEUES = ENABLED_QUEUES


@setup_logging.connect
def on_celery_setup_logging(**kwargs):
    configure_logging(service="gryadka")


def _purge_celery_queues(queue_names: list, context: str = "worker") -> None:
    """Purge specified Celery queues to clear stale tasks from previous deployments."""
    try:
        client = _get_celery_redis_client()
        for queue_name in queue_names:
            try:
                purged_count = client.llen(queue_name)
                if purged_count > 0:
                    client.delete(queue_name)
                    logger.debug(
                        "[%s] Purged %d stale tasks from queue '%s'",
                        context, purged_count, queue_name
                    )
                else:
                    logger.debug("[%s] Queue '%s' is empty, no purge needed", context, queue_name)
            except RedisError as exc:
                logger.warning("[%s] Failed to purge queue '%s': %s", context, queue_name, exc)

        logger.debug("[%s] Queue purge completed for %d queues", context, len(queue_names))
    except RedisError as exc:
        logger.warning("[%s] Error during queue purge: %s", context, exc)


def _get_celery_redis_client():
    global _celery_redis_client
    if _celery_redis_client is None:
        _celery_redis_client = create_internal_redis_client(db=celery_db, decode_responses=False)
    return _celery_redis_client


def _trim_celery_queue(queue_name: str, context: str = "publish") -> None:
    max_length = max(0, int(settings.celery_queue_max_length))
    if max_length <= 0:
        return

    try:
        client = _get_celery_redis_client()
        queue_length = client.llen(queue_name)
        if queue_length <= max_length:
            return
        # Kombu Redis transport pushes new messages to the left and consumes from the right.
        # Keep the newest periodic tasks and drop stale backlog.
        client.ltrim(queue_name, 0, max_length - 1)
        logger.warning(
            "[%s] Trimmed Celery queue '%s' from %d to %d tasks",
            context,
            queue_name,
            queue_length,
            max_length,
        )
    except RedisError as exc:
        logger.warning("[%s] Failed to trim Celery queue '%s': %s", context, queue_name, exc)


@after_task_publish.connect
def on_after_task_publish(sender=None, routing_key=None, **kwargs):
    queue_name = routing_key or kwargs.get("queue")
    if not queue_name or queue_name not in ALL_QUEUES:
        return
    _trim_celery_queue(queue_name, context="publish")


@worker_ready.connect
def on_worker_ready(sender, **kwargs):
    """Purge all Celery queues on worker startup to clear stale tasks from previous deployments."""
    # Purge all queues to prevent accumulation from multiple restarts
    logger.debug("[worker] Worker ready, purging all queues: %s", ALL_QUEUES)
    _purge_celery_queues(ALL_QUEUES, context="worker")


@beat_init.connect
def on_beat_init(sender, **kwargs):
    """Purge all Celery queues on beat scheduler startup to prevent task accumulation."""
    logger.debug("[beat] Beat scheduler starting, purging all queues: %s", ALL_QUEUES)
    _purge_celery_queues(ALL_QUEUES, context="beat")
