"""
Service for interacting with Redis data (SCAN, GET, DELETE).
Handles connection logic (Sentinel/Direct) with pooling and semaphore protection.
"""
import logging
import redis
import asyncio
import threading
import uuid
import json
import time
from typing import List, Dict, Any, Optional, Tuple
from redis.sentinel import Sentinel
from app.config import get_app_config
from app.services.endpoints import parse_host_port
from app.monitor_logging import log_monitor_request
from app.services.sse_manager import sse_manager

logger = logging.getLogger(__name__)


class RedisService:
    @staticmethod
    def _is_sentinel_target(options: Dict[str, Any], nodes: List[Tuple[str, int]], sentinel_port: int) -> bool:
        configured_mode = str(options.get("mode", "")).strip().lower()
        if configured_mode == "sentinel":
            return True
        if configured_mode == "direct":
            return False
        if options.get("masterName"):
            return True
        return any(port == sentinel_port for _, port in nodes)

    def __init__(self):
        # Cache for Redis clients (which manage their own connection pools)
        self._clients: Dict[str, redis.Redis] = {}
        self._clients_lock = threading.RLock()
        self._target_modes: Dict[str, bool] = {}
        # Limit concurrent scans to prevent overwhelming backend or Redis
        self._scan_semaphore = asyncio.Semaphore(3)

    @staticmethod
    def _auth_kwargs(username: Optional[str], password: Optional[str]) -> Dict[str, str]:
        auth_kwargs = {}
        if username:
            auth_kwargs["username"] = username
        if password:
            auth_kwargs["password"] = password
        return auth_kwargs

    @staticmethod
    def _is_stale_master_error(error: Exception) -> bool:
        stale_error_types = (
            getattr(redis.exceptions, "ReadOnlyError", ()),
            redis.exceptions.ConnectionError,
            redis.exceptions.TimeoutError,
        )
        if isinstance(error, stale_error_types):
            return True
        return "READONLY" in str(error).upper()

    def _invalidate_master_clients(self, target_name: str, db: int) -> None:
        client_keys = (f"{target_name}:{db}", f"{target_name}:raw:{db}")
        with self._clients_lock:
            for client_key in client_keys:
                client = self._clients.pop(client_key, None)
                if client:
                    try:
                        client.close()
                    except Exception:
                        pass

    def _with_master_retry(self, target_name: str, db: int, operation):
        try:
            return operation()
        except Exception as error:
            if not self._is_stale_master_error(error):
                raise
            logger.warning(
                "Invalidating cached Redis master client for %s db %s after error: %s",
                target_name,
                db,
                error,
            )
            self._invalidate_master_clients(target_name, db)
            return operation()

    def _get_target_config(self, target_name: str):
        config = get_app_config()
        if not config:
            return None
        return next((t for t in config.redis_targets if t.name == target_name), None)

    def _resolve_target_mode(
        self,
        target_name: str,
        options: Dict[str, Any],
        nodes: List[Tuple[str, int]],
        sentinel_port: int,
        timeout: float,
        username: Optional[str],
        password: Optional[str],
    ) -> bool:
        configured_mode = str(options.get("mode", "")).strip().lower()
        if configured_mode == "sentinel":
            return True
        if configured_mode == "direct":
            return False
        if options.get("masterName"):
            self._target_modes[target_name] = True
            return True

        with self._clients_lock:
            cached_mode = self._target_modes.get(target_name)
        if cached_mode is not None:
            return cached_mode

        if any(port == sentinel_port for _, port in nodes):
            with self._clients_lock:
                self._target_modes[target_name] = True
            return True

        detected_mode = False
        for host, port in nodes:
            try:
                probe = redis.Redis(
                    host=host,
                    port=port,
                    username=username,
                    password=password,
                    decode_responses=True,
                    socket_timeout=timeout,
                    socket_connect_timeout=timeout,
                    retry_on_timeout=True,
                    health_check_interval=30,
                )
                role = str(probe.info("replication").get("role", "")).lower()
                detected_mode = role == "sentinel"
                probe.close()
                break
            except Exception:
                continue

        with self._clients_lock:
            self._target_modes[target_name] = detected_mode
        return detected_mode

    def _get_master_client(self, target_name: str, db: int = 0) -> redis.Redis:
        """
        Get or create a cached Redis client for the Master node.
        """
        client_key = f"{target_name}:{db}"

        with self._clients_lock:
            if client_key in self._clients:
                # Basic ping check could be added here, but redis-py handles reconnects
                return self._clients[client_key]

            target = self._get_target_config(target_name)
            if not target:
                raise ValueError(f"Redis target {target_name} not found")

            options = target.options or {}
            sentinel_port = int(options.get("sentinelPort", 26379) or 26379)
            redis_port = int(options.get("redisPort", 6379) or 6379)
            master_name = options.get("masterName", "mymaster")
            username = target.username
            password = target.password
            timeout = float(options.get("timeoutS", 5.0) or 5.0)

            # Extract nodes
            default_port = redis_port if options.get("mode") == "direct" else sentinel_port
            nodes = [parse_host_port(node, default_port) for node in target.nodes]

            is_sentinel = self._resolve_target_mode(
                target_name, options, nodes, sentinel_port, timeout, username, password
            )

            if is_sentinel:
                try:
                    auth_kwargs = self._auth_kwargs(username, password)
                    sentinel = Sentinel(
                        nodes,
                        socket_timeout=timeout,
                        **auth_kwargs,
                        sentinel_kwargs=auth_kwargs.copy()
                    )
                    master_host, master_port = sentinel.discover_master(master_name)
                    client = redis.Redis(
                        host=master_host,
                        port=master_port,
                        username=username,
                        password=password,
                        decode_responses=True,
                        socket_timeout=timeout,
                        socket_connect_timeout=timeout,
                        db=db,
                        retry_on_timeout=True,
                        health_check_interval=30
                    )
                    self._clients[client_key] = client
                    return client
                except Exception as e:
                    logger.warning(f"Failed to connect via Sentinel for {target_name}: {e}. Falling back to direct node-by-node discovery.")

            # Direct connection fallback: iterate through nodes to find Master
            if not nodes:
                 raise ValueError(f"No nodes configured for {target_name}")

            for host, port in nodes:
                try:
                    actual_port = redis_port if port == sentinel_port else port
                    client = redis.Redis(
                        host=host,
                        port=actual_port,
                        username=username,
                        password=password,
                        decode_responses=True,
                        socket_timeout=timeout,
                        socket_connect_timeout=timeout,
                        db=db,
                        retry_on_timeout=True,
                        health_check_interval=30
                    )
                    # Verify if this node is a master before caching
                    replication_info = client.info("replication")
                    if replication_info.get("role") == "master":
                        self._clients[client_key] = client
                        log_monitor_request(
                            logger,
                            "redis",
                            "discover_master",
                            host=f"{host}:{actual_port}",
                            target_name=target_name,
                        )
                        return client
                    client.close()
                except Exception as e:
                    log_monitor_request(
                        logger,
                        "redis",
                        "discover_master_failed",
                        host=host,
                        target_name=target_name,
                        error=str(e),
                    )
                    continue

            # Absolute fallback: use first node if no master found (allows at least SCAN to work)
            host, port = nodes[0]
            actual_port = redis_port if port == sentinel_port else port
            client = redis.Redis(
                host=host,
                port=actual_port,
                username=username,
                password=password,
                decode_responses=True,
                socket_timeout=timeout,
                socket_connect_timeout=timeout,
                db=db,
                retry_on_timeout=True,
                health_check_interval=30
            )
            self._clients[client_key] = client
            return client

    def _get_master_client_raw(self, target_name: str, db: int = 0) -> redis.Redis:
        """
        Get Redis client without decode_responses=True for binary data handling.
        """
        client_key = f"{target_name}:raw:{db}"

        with self._clients_lock:
            if client_key in self._clients:
                return self._clients[client_key]

            target = self._get_target_config(target_name)
            if not target:
                raise ValueError(f"Redis target {target_name} not found")

            options = target.options or {}
            sentinel_port = int(options.get("sentinelPort", 26379) or 26379)
            redis_port = int(options.get("redisPort", 6379) or 6379)
            master_name = options.get("masterName", "mymaster")
            username = target.username
            password = target.password
            timeout = float(options.get("timeoutS", 5.0) or 5.0)

            # Extract nodes
            nodes = []
            for n in target.nodes:
                if isinstance(n, dict):
                    nodes.append((n.get("host"), int(n.get("port", sentinel_port))))
                else:
                    parts = str(n).split(":")
                    nodes.append((parts[0], int(parts[1]) if len(parts) > 1 else sentinel_port))

            is_sentinel = self._resolve_target_mode(
                target_name, options, nodes, sentinel_port, timeout, username, password
            )

            if is_sentinel:
                try:
                    auth_kwargs = self._auth_kwargs(username, password)
                    sentinel = Sentinel(
                        nodes,
                        socket_timeout=timeout,
                        **auth_kwargs,
                        sentinel_kwargs=auth_kwargs.copy()
                    )
                    master_host, master_port = sentinel.discover_master(master_name)
                    client = redis.Redis(
                        host=master_host,
                        port=master_port,
                        username=username,
                        password=password,
                        decode_responses=False,  # IMPORTANT: No auto-decoding
                        socket_timeout=timeout,
                        socket_connect_timeout=timeout,
                        db=db,
                        retry_on_timeout=True,
                        health_check_interval=30
                    )
                    self._clients[client_key] = client
                    return client
                except Exception as e:
                    logger.warning(f"Failed to connect via Sentinel for {target_name}: {e}. Falling back to direct.")

            # Direct connection fallback
            if not nodes:
                 raise ValueError(f"No nodes configured for {target_name}")

            for host, port in nodes:
                try:
                    actual_port = redis_port if port == sentinel_port else port
                    client = redis.Redis(
                        host=host,
                        port=actual_port,
                        username=username,
                        password=password,
                        decode_responses=False,  # IMPORTANT: No auto-decoding
                        socket_timeout=timeout,
                        socket_connect_timeout=timeout,
                        db=db,
                        retry_on_timeout=True,
                        health_check_interval=30
                    )
                    replication_info = client.info("replication")
                    if replication_info.get("role") == "master":
                        self._clients[client_key] = client
                        log_monitor_request(
                            logger,
                            "redis",
                            "discover_master_raw",
                            host=f"{host}:{actual_port}",
                            target_name=target_name,
                        )
                        return client
                    client.close()
                except Exception as e:
                    log_monitor_request(
                        logger,
                        "redis",
                        "discover_master_raw_failed",
                        host=host,
                        target_name=target_name,
                        error=str(e),
                    )
                    continue

            # Absolute fallback
            host, port = nodes[0]
            actual_port = redis_port if port == sentinel_port else port
            client = redis.Redis(
                host=host,
                port=actual_port,
                username=username,
                password=password,
                decode_responses=False,  # IMPORTANT: No auto-decoding
                socket_timeout=timeout,
                socket_connect_timeout=timeout,
                db=db,
                retry_on_timeout=True,
                health_check_interval=30
            )
            self._clients[client_key] = client
            return client

    def _decode_binary(self, data: bytes, max_size: int = 1048576) -> str:
        """Decode bytes to string, with UTF-8 fallback to hex."""
        if not data:
            return ""

        # Truncate if too large
        display_data = data[:max_size] if len(data) > max_size else data

        try:
            # Try UTF-8
            decoded = display_data.decode('utf-8')
            # Also try JSON
            try:
                return json.loads(decoded)
            except (json.JSONDecodeError, ValueError):
                return decoded
        except UnicodeDecodeError:
            # Fallback to hex
            hex_str = display_data.hex()
            total_size = len(data)
            if len(hex_str) > 200:
                return f"[hex: {hex_str[:200]}... ({total_size} bytes)]"
            return f"[hex: {hex_str} ({total_size} bytes)]"

    def _format_binary_value(self, value: Any) -> Any:
        """Format a binary value (bytes) for display."""
        if isinstance(value, bytes):
            return self._decode_binary(value)
        return value

    async def scan_keys(self, target_name: str, pattern: str, cursor: int = 0, count: int = 1000, db: int = 0, username: str = "Someone") -> Tuple[int, List[Dict[str, Any]]]:
        """
        Scan keys matching pattern with semaphore protection and SSE notification.
        """
        request_id = str(uuid.uuid4())
        await sse_manager.publish({
            "event": "redis.keys.searching",
            "target_type": "redis",
            "target_name": target_name,
            "request_id": request_id,
            "db": db,
            "pattern": pattern,
            "username": username,
            "status": "started"
        })
        try:
            async with self._scan_semaphore:
                # Run the synchronous/blocking part in a thread to keep Event Loop free
                return await asyncio.to_thread(self._scan_sync, target_name, pattern, cursor, count, db)
        finally:
            await sse_manager.publish({
                "event": "redis.keys.searching",
                "target_type": "redis",
                "target_name": target_name,
                "request_id": request_id,
                "status": "finished"
            })

    def _scan_sync(self, target_name: str, pattern: str, cursor: int, count: int, db: int) -> Tuple[int, List[Dict[str, Any]]]:
        def operation():
            return self._scan_sync_once(target_name, pattern, cursor, count, db)

        return self._with_master_retry(target_name, db, operation)

    def _scan_sync_once(self, target_name: str, pattern: str, cursor: int, count: int, db: int) -> Tuple[int, List[Dict[str, Any]]]:
        started_at = time.perf_counter()
        client = self._get_master_client(target_name, db)
        requested_results = max(1, min(count, 1000))
        scan_count = max(100, min(count, 1000))
        all_keys: List[str] = []
        current_cursor = cursor
        iterations = 0
        max_iterations = max(50, min(500, requested_results * 2))

        scan_started_at = time.perf_counter()
        while iterations < max_iterations:
            current_cursor, batch_keys = client.scan(cursor=current_cursor, match=pattern, count=scan_count)
            if batch_keys:
                all_keys.extend(batch_keys)
            iterations += 1
            if current_cursor == 0 or len(all_keys) >= requested_results:
                break

        scan_elapsed_ms = int((time.perf_counter() - scan_started_at) * 1000)
        all_keys = all_keys[:requested_results]
        results = []
        if all_keys:
            # Pipeline TYPE and TTL for fetched keys
            pipeline_started_at = time.perf_counter()
            pipe = client.pipeline(transaction=False)
            for key in all_keys:
                pipe.type(key)
                pipe.ttl(key)

            pipeline_res = pipe.execute()
            pipeline_elapsed_ms = int((time.perf_counter() - pipeline_started_at) * 1000)

            # Combine results
            for i, key in enumerate(all_keys):
                results.append({
                    "key": key,
                    "type": pipeline_res[i * 2],
                    "ttl": pipeline_res[i * 2 + 1]
                })
        else:
            pipeline_elapsed_ms = 0

        total_elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        log_monitor_request(
            logger,
            "redis",
            "scan_keys",
            host=target_name,
            database=str(db),
            pattern=pattern,
            cursor_in=cursor,
            cursor_out=current_cursor,
            iterations=iterations,
            found=len(results),
            scan_ms=scan_elapsed_ms,
            pipeline_ms=pipeline_elapsed_ms,
            total_ms=total_elapsed_ms,
        )

        return current_cursor, results

    async def delete_keys(self, target_name: str, keys: List[str], db: int = 0, username: str = "Someone") -> int:
        """
        Delete specific keys using UNLINK (async wrapper) with SSE notifications.
        """
        if not keys:
            return 0

        request_id = str(uuid.uuid4())
        await sse_manager.publish({
            "event": "redis.keys.deleting",
            "target_type": "redis",
            "target_name": target_name,
            "request_id": request_id,
            "db": db,
            "count": len(keys),
            "username": username,
            "status": "started"
        })

        try:
            result = await asyncio.to_thread(self._delete_sync, target_name, keys, db)
            return result
        finally:
            await sse_manager.publish({
                "event": "redis.keys.deleting",
                "target_type": "redis",
                "target_name": target_name,
                "request_id": request_id,
                "status": "finished"
            })

    async def delete_keys_by_pattern(self, target_name: str, pattern: str, db: int = 0, username: str = "Someone") -> int:
        """
        Delete all keys matching a pattern using SCAN + UNLINK loop.
        """
        request_id = str(uuid.uuid4())
        # Notify start
        await sse_manager.publish({
            "event": "redis.keys.deleting",
            "target_type": "redis",
            "target_name": target_name,
            "request_id": request_id,
            "db": db,
            "pattern": pattern,
            "username": username,
            "status": "started"
        })

        try:
            # Run the heavy lifting in a thread
            total_deleted = await asyncio.to_thread(self._delete_by_pattern_sync, target_name, pattern, db)
            return total_deleted
        finally:
            await sse_manager.publish({
                "event": "redis.keys.deleting",
                "target_type": "redis",
                "target_name": target_name,
                "request_id": request_id,
                "status": "finished"
            })

    def _delete_by_pattern_sync(self, target_name: str, pattern: str, db: int) -> int:
        def operation():
            client = self._get_master_client(target_name, db)
            cursor = 0
            total_deleted = 0
            batch_size = 1000

            while True:
                cursor, keys = client.scan(cursor=cursor, match=pattern, count=batch_size)
                if keys:
                    # Use pipeline for UNLINK to ensure atomicity within the batch and speed
                    pipe = client.pipeline()
                    pipe.unlink(*keys)
                    pipe.execute()
                    total_deleted += len(keys)

                if cursor == 0:
                    break

            return total_deleted

        return self._with_master_retry(target_name, db, operation)

    def _delete_sync(self, target_name: str, keys: List[str], db: int) -> int:
        def operation():
            client = self._get_master_client(target_name, db)
            return client.unlink(*keys)

        return self._with_master_retry(target_name, db, operation)

    async def get_key_value(self, target_name: str, key: str, db: int = 0) -> Dict[str, Any]:
        """
        Get value of a specific key with type-specific formatting.
        Returns: {key, type, ttl, value, size, has_more, total_count}
        """
        return await asyncio.to_thread(self._get_key_value_sync, target_name, key, db)

    def _get_key_value_sync(self, target_name: str, key: str, db: int) -> Dict[str, Any]:
        def operation():
            return self._get_key_value_sync_once(target_name, key, db)

        return self._with_master_retry(target_name, db, operation)

    def _get_key_value_sync_once(self, target_name: str, key: str, db: int) -> Dict[str, Any]:
        # Get raw client (without decode_responses) to handle binary data
        raw_client = self._get_master_client_raw(target_name, db)

        # Get key info using regular client (for commands that return strings)
        client = self._get_master_client(target_name, db)

        key_type = client.type(key)
        ttl = client.ttl(key)
        size = 0
        value = None
        has_more = False
        total_count = 0

        MAX_ITEMS = 500  # Max items to return for collections
        MAX_STRING_SIZE = 1048576  # 1MB for strings

        if key_type == 'string':
            # Use raw client to get bytes
            raw_value = raw_client.get(key)
            if raw_value:
                if isinstance(raw_value, bytes):
                    size = len(raw_value)
                    # Try to decode as UTF-8, fallback to hex
                    value = self._decode_binary(raw_value, MAX_STRING_SIZE)
                    has_more = size > MAX_STRING_SIZE
                else:
                    # It's already a string (shouldn't happen with raw client, but handle anyway)
                    size = len(raw_value.encode('utf-8')) if isinstance(raw_value, str) else len(raw_value)
                    if size > MAX_STRING_SIZE:
                        value = str(raw_value)[:MAX_STRING_SIZE] + '... [truncated]'
                        has_more = True
                    else:
                        value = str(raw_value)
            else:
                value = None

        elif key_type == 'hash':
            total_count = client.hlen(key)
            cursor, items = raw_client.hscan(key, cursor=0, count=MAX_ITEMS)
            item_pairs = list(items.items())[:MAX_ITEMS]
            if cursor != 0 or total_count > len(item_pairs):
                has_more = True
            if item_pairs:
                # Decode keys as strings (they should be UTF-8), values as binary
                value = {}
                for k, v in item_pairs:
                    key_str = k.decode('utf-8') if isinstance(k, bytes) else str(k)
                    value[key_str] = self._format_binary_value(v)
            else:
                value = {}

        elif key_type == 'list':
            total_count = client.llen(key)
            # Get first MAX_ITEMS with their indices
            items_raw = raw_client.lrange(key, 0, MAX_ITEMS - 1)
            if items_raw:
                if len(items_raw) >= MAX_ITEMS and total_count > MAX_ITEMS:
                    has_more = True
                value = [{"index": i, "value": self._format_binary_value(v)} for i, v in enumerate(items_raw)]
            else:
                value = []

        elif key_type == 'set':
            total_count = client.scard(key)
            cursor, members_raw = raw_client.sscan(key, cursor=0, count=MAX_ITEMS)
            members_raw = list(members_raw)[:MAX_ITEMS]
            if cursor != 0 or total_count > len(members_raw):
                has_more = True
            if members_raw:
                value = [self._format_binary_value(m) for m in members_raw]
            else:
                value = []

        elif key_type == 'zset':
            total_count = client.zcard(key)
            # Get first MAX_ITEMS with scores
            items_raw = raw_client.zrange(key, 0, MAX_ITEMS - 1, withscores=True)
            if items_raw:
                if len(items_raw) >= MAX_ITEMS and total_count > MAX_ITEMS:
                    has_more = True
                value = [{"member": self._format_binary_value(m), "score": s} for m, s in items_raw]
            else:
                value = []

        return {
            "key": key,
            "type": key_type,
            "ttl": ttl,
            "value": value,
            "size": size if key_type == 'string' else total_count,
            "has_more": has_more,
            "total_count": total_count
        }

    def _format_value(self, value: Any) -> Any:
        """Try to parse as JSON for pretty display, handle binary data as hex."""
        if not isinstance(value, (str, bytes)):
            return value

        # Handle bytes (binary data)
        if isinstance(value, bytes):
            try:
                # Try UTF-8 first
                decoded = value.decode('utf-8')
                # If successful, try JSON
                try:
                    return json.loads(decoded)
                except (json.JSONDecodeError, ValueError):
                    return decoded
            except UnicodeDecodeError:
                # Fallback to hex representation
                hex_str = value.hex()
                if len(hex_str) > 200:
                    return f"[hex: {hex_str[:200]}... ({len(value)} bytes)]"
                return f"[hex: {hex_str} ({len(value)} bytes)]"

        # Handle string
        try:
            parsed = json.loads(value)
            return parsed
        except (json.JSONDecodeError, ValueError):
            return value

redis_service = RedisService()
