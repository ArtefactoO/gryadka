"""
FastAPI main application
"""
import asyncio
import logging
import time
from pathlib import Path

from fastapi import FastAPI, Request, Depends
from contextlib import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import AppConfig, app_config, get_app_config, settings
from app.api import kafka, postgresql, argo_rollout, auth, clusters, health, meta, stats, websocket, targets, rabbitmq, redis, haproxy, redpanda, elasticsearch, emqx, events, registry
from app.services.websocket_manager import websocket_manager
from app.services.cache import CacheService
from app.services.sse_manager import sse_manager
from app.logging_setup import configure_logging

# Configure logging first so startup progress is visible immediately
configure_logging(service="gryadka")
logger = logging.getLogger(__name__)
request_logger = logging.getLogger("app.request")
event_loop_logger = logging.getLogger("app.event_loop")
logger.info("Loading Gryadka app...")

# Initialize app config (update global)
try:
    import app.config as config_module
    config_module.app_config = AppConfig(settings.config_path)
    logger.info(f"Loaded configuration: {len(config_module.app_config.clusters)} clusters, {len(config_module.app_config.users)} users")
except Exception as e:
    logger.error(f"Failed to load configuration: {e}")
    import app.config as config_module
    config_module.app_config = None

background_tasks = set()


def start_background(coroutine):
    task = asyncio.create_task(coroutine)
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)
    return task


@asynccontextmanager
async def lifespan(application):
    await startup_event()
    try:
        yield
    finally:
        await shutdown_event()


app = FastAPI(
    title="Gryadka API",
    lifespan=lifespan,
    description="Monitoring system",
    version="2.0.0",
)

# Signed, short-lived OAuth state/nonce/PKCE session; provider tokens are never persisted.
app.add_middleware(SessionMiddleware, secret_key=settings.jwt_secret, session_cookie="gryadka_oidc_state",
                   max_age=300, same_site="lax", https_only=settings.public_base_url.startswith("https://"))

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.public_base_url.rstrip("/")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _request_log_payload(request: Request, status_code: int, duration_ms: float) -> dict:
    client = request.client.host if request.client else None
    return {
        "method": request.method,
        "path": request.url.path,
        "status_code": status_code,
        "duration_ms": round(duration_ms, 2),
        "client_addr": client,
    }


@app.middleware("http")
async def log_slow_http_requests(request: Request, call_next):
    """Log slow API/readiness requests without restoring noisy access logs."""
    start = time.perf_counter()
    status_code = 500
    error_logged = False
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    except Exception:
        duration_ms = (time.perf_counter() - start) * 1000
        error_logged = True
        request_logger.exception(
            "HTTP request failed",
            extra=_request_log_payload(request, status_code, duration_ms),
        )
        raise
    finally:
        if not error_logged:
            duration_ms = (time.perf_counter() - start) * 1000
            threshold_ms = (
                settings.readiness_slow_log_threshold_ms
                if request.url.path == "/api/ready"
                else settings.slow_request_log_threshold_ms
            )
            if duration_ms >= threshold_ms or status_code >= 500:
                level = logging.ERROR if status_code >= 500 else logging.WARNING
                message = "Slow HTTP request" if duration_ms >= threshold_ms else "HTTP request returned error"
                request_logger.log(
                    level,
                    message,
                    extra={
                        **_request_log_payload(request, status_code, duration_ms),
                        "threshold_ms": threshold_ms,
                    },
                )

# Include routers (must be before static files)
app.include_router(health.router)
app.include_router(meta.router)
app.include_router(auth.router)
app.include_router(targets.router, dependencies=[Depends(auth.require_permission("targets.read"))])
app.include_router(clusters.router, dependencies=[Depends(auth.require_permission("mongodb.read"))])
app.include_router(rabbitmq.router, dependencies=[Depends(auth.require_permission("rabbitmq.read"))])
app.include_router(redis.router, dependencies=[Depends(auth.require_permission("redis.read"))])
app.include_router(haproxy.router, dependencies=[Depends(auth.require_permission("haproxy.read"))])
app.include_router(redpanda.router, dependencies=[Depends(auth.require_permission("redpanda.read"))])
app.include_router(elasticsearch.router, dependencies=[Depends(auth.require_permission("elasticsearch.read"))])
app.include_router(emqx.router, dependencies=[Depends(auth.require_permission("emqx.read"))])
app.include_router(argo_rollout.router, dependencies=[Depends(auth.require_permission("argo_rollout.read"))])
app.include_router(registry.router, dependencies=[Depends(auth.require_permission("addresses.read"))])
app.include_router(registry.legacy_router, dependencies=[Depends(auth.require_permission("addresses.read"))])
app.include_router(postgresql.router, dependencies=[Depends(auth.require_permission("postgresql.read"))])
app.include_router(kafka.router, dependencies=[Depends(auth.require_permission("kafka.read"))])
app.include_router(stats.router, dependencies=[Depends(auth.require_permission("targets.read"))])
app.include_router(websocket.router)
app.include_router(events.router)

# Serve frontend static files (must be last, after all API routes)
frontend_dist = Path("/app/frontend/dist")
if frontend_dist.exists():
    # Mount static files, but API routes take precedence
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="static")
    logger.info("Frontend static files mounted")
else:
    logger.warning(f"Frontend dist not found at {frontend_dist}")


async def startup_event():
    """Initialize data collection on startup (non-blocking)"""
    logger.info("Starting up Gryadka...")
    argo_rollout_namespace = None

    # Initialize Argo Rollout service if configured
    try:
        import app.config as config_module
        if config_module.app_config:
            for monitor in getattr(config_module.app_config, 'monitors', []):
                if monitor.get('name') in {'argo_rollout', 'rollouts'}:
                    from app.services.argo_rollout_service import initialize_argo_rollout_service
                    monitor_config = monitor.get('config', {}) or {}
                    targets = monitor_config.get('targets', []) if isinstance(monitor_config, dict) else []
                    namespace = 'default'
                    if isinstance(targets, list) and targets:
                        namespace = targets[0].get('namespace', 'default')
                    elif isinstance(monitor_config, dict):
                        namespace = monitor_config.get('namespace', 'default')
                    argo_rollout_namespace = namespace
                    await initialize_argo_rollout_service(namespace)
                    logger.info(f"ArgoRolloutService initialized for namespace: {namespace}")
                    break
    except Exception as e:
        logger.error(f"Failed to initialize ArgoRolloutService: {e}")

    # Start background task to check for WebSocket notifications
    start_background(monitor_event_loop_lag())
    start_background(check_websocket_notifications())
    # K8s Watch disabled: pod list is refreshed by a lightweight snapshot loop.
    # start_background(haproxy_k8s_watcher.run_forever())
    # Evaluate running K8s operations periodically so UI gets status updates via SSE with minimal delay
    start_background(background_refresh_haproxy_operations())
    system_cache = CacheService(
        db=settings.redis_system_db
        if settings.redis_system_db is not None
        else (settings.redis_cache_db if settings.redis_cache_db is not None else settings.redis_db)
    )
    sse_manager.configure_redis(system_cache.redis_client)
    await sse_manager.start()
    start_background(background_refresh_haproxy_k8s_pods())
    if argo_rollout_namespace:
        from app.services.argo_rollout_k8s_watcher import argo_rollout_k8s_watcher
        start_background(argo_rollout_k8s_watcher.run_forever(argo_rollout_namespace))

    # Start data collection in background (non-blocking)
    start_background(background_data_collection())


async def monitor_event_loop_lag():
    """Warn when the app event loop is blocked long enough to affect readiness."""
    interval_s = max(0.1, float(settings.event_loop_lag_check_interval_s))
    threshold_ms = max(1.0, float(settings.event_loop_lag_log_threshold_ms))
    loop = asyncio.get_running_loop()
    expected = loop.time() + interval_s

    while True:
        try:
            await asyncio.sleep(interval_s)
            now = loop.time()
            lag_ms = max(0.0, (now - expected) * 1000)
            if lag_ms >= threshold_ms:
                event_loop_logger.warning(
                    "Event loop lag detected",
                    extra={
                        "lag_ms": round(lag_ms, 2),
                        "threshold_ms": threshold_ms,
                        "check_interval_s": interval_s,
                    },
                )
            expected = now + interval_s
        except asyncio.CancelledError:
            break
        except Exception as e:
            event_loop_logger.error("Event loop lag monitor failed: %s", e)
            await asyncio.sleep(interval_s)


async def background_data_collection():
    """Background task to collect initial data (non-blocking startup). Collectors are imported here to avoid slowing app startup."""
    import app.config as config_module
    import concurrent.futures

    # Small delay to ensure server is ready to accept requests
    await asyncio.sleep(0.5)

    if config_module.app_config:
        logger.info("Starting background data collection for all targets...")
        try:
            # Lazy import: pull in tasks (and thus all collectors) only when running first collection
            from app.services.tasks import (
                _collect_all_clusters_sync,
                _collect_all_rabbitmq_sync,
                _collect_all_redis_sync,
                _collect_all_postgresql_sync,
                _collect_all_kafka_sync,
                _collect_all_haproxy_sync,
                _collect_all_redpanda_sync,
                _collect_all_elasticsearch_sync,
                _collect_all_emqx_sync,
            )
            current_config = get_app_config()
            enabled_monitor_types = set(current_config.get_enabled_monitor_types()) if current_config else set()
            collector_specs = [
                ("mongodb", _collect_all_clusters_sync),
                ("rabbitmq", _collect_all_rabbitmq_sync),
                ("redis", _collect_all_redis_sync),
                ("postgresql", _collect_all_postgresql_sync),
                ("kafka", _collect_all_kafka_sync),
                ("haproxy", _collect_all_haproxy_sync),
                ("redpanda", _collect_all_redpanda_sync),
                ("elasticsearch", _collect_all_elasticsearch_sync),
                ("emqx", _collect_all_emqx_sync),
            ]
            active_collectors = [
                (name, fn) for name, fn in collector_specs if name in enabled_monitor_types
            ]
            if not active_collectors:
                logger.info("No enabled monitor types found, skipping background data collection")
                return
            loop = asyncio.get_event_loop()
            with concurrent.futures.ThreadPoolExecutor() as executor:
                futures = [
                    loop.run_in_executor(executor, fn)
                    for _name, fn in active_collectors
                ]
                results = await asyncio.gather(*futures, return_exceptions=True)

                updated_counts = {}
                for i, result in enumerate(results):
                    collector_name = active_collectors[i][0]
                    if isinstance(result, Exception):
                        logger.error("Error during background data collection (%s): %s", collector_name, result)
                        updated_counts[collector_name] = 0
                    else:
                        updated_counts[collector_name] = len(result) if isinstance(result, list) else 0

                logger.info(
                    "Background data collection completed: %s",
                    updated_counts,
                )
        except Exception as e:
            logger.error(f"Error during background data collection: {e}")
    else:
        logger.warning("App config not loaded, skipping initial data collection")


HAPROXY_OPERATIONS_REFRESH_INTERVAL_S = 1.5


async def background_refresh_haproxy_operations():
    """Periodically evaluate running K8s operations and push SSE so UI shows status with minimal delay."""
    while True:
        try:
            await asyncio.sleep(HAPROXY_OPERATIONS_REFRESH_INTERVAL_S)
            await haproxy.refresh_running_operations()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.debug("Background HAProxy operations refresh failed: %s", e)


async def background_refresh_haproxy_k8s_pods():
    """Refresh HAProxy K8s pod snapshots outside user GET requests."""
    await asyncio.sleep(0.2)
    while True:
        try:
            use_fast_interval = await haproxy.refresh_haproxy_k8s_pods_snapshots()
            await asyncio.sleep(
                haproxy.HAPROXY_K8S_PODS_FAST_REFRESH_INTERVAL_S
                if use_fast_interval
                else haproxy.HAPROXY_K8S_PODS_REFRESH_INTERVAL_S
            )
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.debug("Background HAProxy K8s pod refresh failed: %s", e)
            await asyncio.sleep(haproxy.HAPROXY_K8S_PODS_REFRESH_INTERVAL_S)


async def check_websocket_notifications():
    """Background task to check Redis for WebSocket notifications"""
    system_cache = CacheService(
        db=settings.redis_system_db
        if settings.redis_system_db is not None
        else (settings.redis_cache_db if settings.redis_cache_db is not None else settings.redis_db)
    )
    legacy_cache = CacheService(
        db=settings.redis_db
    )
    while True:
        try:
            await asyncio.sleep(1)  # Check every second
            notify_data = await asyncio.to_thread(system_cache.redis_client.getdel, "websocket:notify")
            # Backward compatibility for mixed rollout where workers/app use different DB mapping.
            if not notify_data and system_cache.redis_client.connection_pool.connection_kwargs.get("db") != settings.redis_db:
                notify_data = await asyncio.to_thread(legacy_cache.redis_client.getdel, "websocket:notify")
            if notify_data:
                clusters = notify_data.split(",")
                await websocket_manager.notify_data_updated(clusters)

        except Exception as e:
            logger.error(f"Error in WebSocket notification checker: {e}")
            await asyncio.sleep(5)  # Wait longer on error


async def shutdown_event():
    """Cleanup on shutdown"""
    logger.info("Shutting down Gryadka...")
    pending = list(background_tasks)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    await sse_manager.stop()

    # Shutdown Argo Rollout service
    try:
        from app.services.argo_rollout_service import shutdown_argo_rollout_service
        await shutdown_argo_rollout_service()
        logger.info("ArgoRolloutService shutdown")
    except Exception as e:
        logger.error(f"Error shutting down ArgoRolloutService: {e}")
