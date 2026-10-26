"""
Service for collecting data from MongoDB clusters
"""
import logging
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta, timezone
import time
from pymongo import MongoClient


def _utc_isoformat(dt: Optional[datetime]) -> Optional[str]:
    """Return ISO format string in UTC so that JS Date() parses it as UTC (not local)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    s = dt.isoformat()
    return s.replace("+00:00", "Z") if s.endswith("+00:00") else s
from pymongo.errors import AutoReconnect, ServerSelectionTimeoutError, OperationFailure
from collections import defaultdict

from app.models.cluster import Node, NodeSize, DatabaseUser, PrimaryHistory
from app.monitor_logging import log_collector_progress, log_monitor_request
from app.services.infrastructure_metrics import enrich_node_resources

logger = logging.getLogger(__name__)


def _log_mongo_request(host: str, command: str, database: str = "admin", **kwargs):
    log_monitor_request(
        logger,
        "mongodb",
        command,
        host=host,
        database=database,
        **kwargs,
    )


class MongoCollector:
    """Service for collecting MongoDB cluster data"""

    def __init__(self):
        # Lightweight in-process cache to reduce repeated per-node calls.
        self._size_cache_ttl_s = 60
        self._version_cache_ttl_s = 3600
        self._node_size_cache: Dict[str, Tuple[float, Optional[Dict[str, int]]]] = {}
        self._node_version_cache: Dict[str, Tuple[float, Optional[str]]] = {}

    def _normalize_host(self, host: str) -> str:
        return host if ':' in host else f"{host}:27017"

    def _extract_node_connection_params(
        self,
        node_config,
        default_username: Optional[str] = None,
        default_password: Optional[str] = None,
    ) -> Tuple[str, Optional[str], Optional[str]]:
        if isinstance(node_config, dict):
            host = node_config.get('host', node_config) if 'host' in node_config else str(node_config)
            username = node_config.get('username', default_username)
            password = node_config.get('password', default_password)
        else:
            host = str(node_config)
            username = default_username
            password = default_password
        return self._normalize_host(host), username, password

    def _build_client(self, host: str, username: Optional[str], password: Optional[str]) -> MongoClient:
        return MongoClient(
            host=host,
            username=username,
            password=password,
            serverSelectionTimeoutMS=self._get_server_selection_timeout_ms(),
            connect=False,
            directConnection=True,
        )

    def _is_no_replication_error(self, error: Exception) -> bool:
        if isinstance(error, OperationFailure):
            message = (str(error) or "").lower()
            if error.code in (76, 94, 13436):
                return True
            return any(
                marker in message
                for marker in (
                    "not running with --replset",
                    "no replication enabled",
                    "replication not enabled",
                    "not yet initialized for replica set",
                )
            )
        return False

    def _detect_topology(self, client: MongoClient, host: str) -> Dict:
        _log_mongo_request(host, "hello")
        try:
            hello = client.admin.command("hello")
        except OperationFailure:
            _log_mongo_request(host, "isMaster")
            hello = client.admin.command("isMaster")

        set_name = hello.get("setName")
        if set_name:
            _log_mongo_request(host, "replSetGetStatus")
            rs_status = client.admin.command("replSetGetStatus")
            _log_mongo_request(host, "replSetGetConfig")
            rs_config = client.admin.command("replSetGetConfig")
            return {
                "mode": "replicaset",
                "hello": hello,
                "rs_status": rs_status,
                "rs_config": rs_config,
            }

        return {
            "mode": "standalone",
            "hello": hello,
            "rs_status": None,
            "rs_config": None,
        }

    def _build_standalone_node_data(
        self,
        host: str,
        client: MongoClient,
        username: Optional[str],
        password: Optional[str],
        exclude_databases: List[str],
    ) -> Dict:
        _log_mongo_request(host, "serverStatus")
        server_status = client.admin.command("serverStatus")
        version = self._collect_mongo_version_for_host(host, username, password)
        size_info = self._collect_size_for_host(host, username, password, exclude_databases)
        primary_metrics = self._collect_primary_metrics(client, host, None)

        node_data = {
            'name': host,
            'stateStr': 'STANDALONE',
            'status': 'UP',
            'lag': 0,
            'priority': 0,
            'hidden': 0,
            'uptime': int(server_status.get('uptime', 0)) if server_status.get('uptime') is not None else None,
            'sync_source_host': None,
            'mongo_version': version,
            'self': True,
        }
        if size_info:
            node_data['size'] = size_info
        if primary_metrics:
            node_data['primary_metrics'] = primary_metrics
        return node_data

    def _connect_for_cluster(self, cluster_config) -> Tuple[Optional[MongoClient], Optional[str], Optional[Dict], Optional[Tuple[Optional[str], Optional[str]]]]:
        for node_config in cluster_config.nodes:
            client = None
            host = "unknown"
            try:
                host, username, password = self._extract_node_connection_params(
                    node_config,
                    cluster_config.username,
                    cluster_config.password,
                )
                client = self._build_client(host, username, password)
                _log_mongo_request(host, "ping")
                client.admin.command('ping')
                topology = self._detect_topology(client, host)
                return client, host, topology, (username, password)
            except Exception as e:
                logger.warning(f"Could not connect to node {host}: {e}")
                if client:
                    try:
                        client.close()
                    except Exception:
                        pass
        return None, None, None, None

    def _resolve_read_host(self, cluster_config) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
        client, host, topology, credentials = self._connect_for_cluster(cluster_config)
        if not client or not host or not topology or not credentials:
            return None, None, None, None

        username, password = credentials
        try:
            if topology["mode"] == "standalone":
                return host, "standalone", username, password

            rs_status = topology["rs_status"] or {}
            for member in rs_status.get('members', []):
                if member.get('stateStr') == 'PRIMARY':
                    return self._normalize_host(member.get('name', host)), "replicaset", username, password

            return host, "replicaset", username, password
        finally:
            try:
                client.close()
            except Exception:
                pass

    def step_down_primary(self, cluster_name: str, stepdown_seconds: int = 60) -> Dict:
        """Run replSetStepDown against the current PRIMARY of a configured cluster."""
        from app.config import get_app_config

        current_config = get_app_config()
        if not current_config:
            raise ValueError("App config is not initialized")

        cluster_config = current_config.get_cluster_by_name(cluster_name)
        if not cluster_config:
            raise ValueError(f"Cluster {cluster_name} not found")

        client, host, topology, credentials = self._connect_for_cluster(cluster_config)
        if not client or not host or not topology or not credentials:
            raise ValueError(f"Could not connect to any node in cluster {cluster_name}")

        username, password = credentials
        primary_host = host
        primary_client = client
        close_primary_client = False
        try:
            if topology["mode"] != "replicaset":
                raise ValueError(f"Cluster {cluster_name} is not a replica set")

            primary_member = next(
                (member for member in (topology.get("rs_status") or {}).get("members", []) if member.get("stateStr") == "PRIMARY"),
                None,
            )
            if not primary_member or not primary_member.get("name"):
                raise ValueError(f"Could not determine PRIMARY for cluster {cluster_name}")

            primary_host = self._normalize_host(primary_member["name"])
            primary_username, primary_password = username, password
            for node_config in cluster_config.nodes:
                node_host, node_username, node_password = self._extract_node_connection_params(
                    node_config,
                    cluster_config.username,
                    cluster_config.password,
                )
                if node_host == primary_host:
                    primary_username = node_username
                    primary_password = node_password
                    break

            if primary_host != host:
                primary_client = self._build_client(primary_host, primary_username, primary_password)
                close_primary_client = True
                _log_mongo_request(primary_host, "ping")
                primary_client.admin.command("ping")

            _log_mongo_request(primary_host, "replSetStepDown", seconds=stepdown_seconds)
            try:
                result = primary_client.admin.command("replSetStepDown", stepdown_seconds)
            except AutoReconnect as exc:
                result = {"ok": 1, "note": str(exc)}
            except OperationFailure as exc:
                # Successful stepdown can close the connection or return a "not primary" style error
                # after the primary has already stepped down.
                message = str(exc).lower()
                if "not primary" not in message and "not master" not in message and "stepped down" not in message:
                    raise
                result = {"ok": 1, "note": str(exc)}

            return {
                "cluster": cluster_name,
                "previous_primary": primary_host,
                "stepdown_seconds": stepdown_seconds,
                "result": result,
            }
        finally:
            try:
                if close_primary_client:
                    primary_client.close()
                client.close()
            except Exception:
                pass

    def _get_server_selection_timeout_ms(self) -> int:
        """Get server selection timeout from config"""
        from app.config import get_app_config
        current_config = get_app_config()
        return current_config.server_selection_timeout_ms if current_config else 100

    def _get_cached_size(self, host: str) -> Optional[Dict[str, int]]:
        entry = self._node_size_cache.get(host)
        if not entry:
            return None
        ts, value = entry
        if time.time() - ts <= self._size_cache_ttl_s:
            return value
        return None

    def _set_cached_size(self, host: str, value: Optional[Dict[str, int]]) -> None:
        self._node_size_cache[host] = (time.time(), value)

    def _get_cached_version(self, host: str) -> Optional[str]:
        entry = self._node_version_cache.get(host)
        if not entry:
            return None
        ts, value = entry
        if time.time() - ts <= self._version_cache_ttl_s:
            return value
        return None

    def _set_cached_version(self, host: str, value: Optional[str]) -> None:
        self._node_version_cache[host] = (time.time(), value)

    def _collect_size_for_host(
        self,
        host: str,
        username: Optional[str],
        password: Optional[str],
        exclude_databases: List[str],
    ) -> Optional[Dict[str, int]]:
        """Collect node size information for a specific replica set member."""
        cached = self._get_cached_size(host)
        if cached is not None:
            return cached

        client = None
        try:
            node_host = host if ':' in host else f"{host}:27017"
            client = MongoClient(
                host=node_host,
                username=username,
                password=password,
                serverSelectionTimeoutMS=self._get_server_selection_timeout_ms(),
                connect=False,
                directConnection=True,
            )
            _log_mongo_request(node_host, "listDatabases")
            list_databases = client.admin.command("listDatabases")

            total_size = list_databases.get('totalSize', 0)
            local_db_size = 0
            for db in list_databases.get('databases', []):
                db_name = db.get('name')
                if db_name in exclude_databases:
                    total_size -= db.get('sizeOnDisk', 0)
                if db_name == 'local':
                    local_db_size = db.get('sizeOnDisk', 0)

            oplog_size = 0
            try:
                _log_mongo_request(node_host, "collStats", database="local", collection="oplog.rs")
                oplog_stats = client["local"].command("collStats", "oplog.rs")
                oplog_size = int(oplog_stats.get("storageSize") or oplog_stats.get("size") or 0)
            except Exception as e:
                logger.warning(f"Could not get oplog collStats for node {host}: {e}")
                oplog_size = int(local_db_size or 0)

            result = {
                'oplog': oplog_size,
                'data': max(0, total_size),
            }
            self._set_cached_size(host, result)
            return result
        except Exception as e:
            logger.warning(f"Could not get size info for node {host}: {e}")
            self._set_cached_size(host, None)
            return None
        finally:
            if client:
                try:
                    client.close()
                except Exception:
                    pass

    def _collect_mongo_version_for_host(
        self,
        host: str,
        username: Optional[str],
        password: Optional[str],
    ) -> Optional[str]:
        """Collect MongoDB version for a specific replica set member."""
        cached = self._get_cached_version(host)
        if cached is not None:
            return cached

        client = None
        try:
            node_host = host if ':' in host else f"{host}:27017"
            client = MongoClient(
                host=node_host,
                username=username,
                password=password,
                serverSelectionTimeoutMS=self._get_server_selection_timeout_ms(),
                connect=False,
                directConnection=True,
            )
            _log_mongo_request(node_host, "buildInfo")
            build_info = client.admin.command("buildInfo")
            version = build_info.get("version")
            result = str(version) if version else None
            self._set_cached_version(host, result)
            return result
        except Exception as e:
            logger.warning(f"Could not get MongoDB version for node {host}: {e}")
            self._set_cached_version(host, None)
            return None
        finally:
            if client:
                try:
                    client.close()
                except Exception:
                    pass

    def _collect_feature_compatibility_version(self, client: MongoClient, host: str) -> Optional[str]:
        """Collect cluster FCV from PRIMARY node."""
        try:
            _log_mongo_request(host, "getParameter", featureCompatibilityVersion=1)
            response = client.admin.command({"getParameter": 1, "featureCompatibilityVersion": 1})
            fcv = response.get("featureCompatibilityVersion")
            if isinstance(fcv, dict):
                return fcv.get("version") or fcv.get("targetVersion")
            if isinstance(fcv, str):
                return fcv
            return None
        except Exception as e:
            logger.warning(f"Could not get featureCompatibilityVersion from {host}: {e}")
            return None

    def _collect_primary_metrics(self, client: MongoClient, host: str, rs_config: Optional[Dict]) -> Dict:
        """Collect PRIMARY-only metrics and useful replica set settings."""
        metrics: Dict = {}
        try:
            _log_mongo_request(host, "collStats", database="local", collection="oplog.rs")
            oplog_stats = client["local"].command("collStats", "oplog.rs")
            max_size = oplog_stats.get("maxSize")
            used_size = oplog_stats.get("storageSize") or oplog_stats.get("size")
            if isinstance(max_size, (int, float)):
                metrics["oplog_configured_bytes"] = int(max_size)
            if isinstance(used_size, (int, float)):
                metrics["oplog_used_bytes"] = int(used_size)
        except Exception as e:
            logger.warning(f"Could not get oplog stats from {host}: {e}")

        try:
            oplog_coll = client["local"]["oplog.rs"]
            _log_mongo_request(host, "find", database="local", collection="oplog.rs", sort="$natural:1", limit=1)
            # Add timeout for oplog find operations (max 5 seconds)
            first_doc = next(oplog_coll.find({}, {"ts": 1, "_id": 0}).sort("$natural", 1).limit(1).max_time_ms(5000), None)
            _log_mongo_request(host, "find", database="local", collection="oplog.rs", sort="$natural:-1", limit=1)
            last_doc = next(oplog_coll.find({}, {"ts": 1, "_id": 0}).sort("$natural", -1).limit(1).max_time_ms(5000), None)
            if first_doc and last_doc and "ts" in first_doc and "ts" in last_doc:
                start_ts = first_doc["ts"]
                end_ts = last_doc["ts"]
                if hasattr(start_ts, "time") and hasattr(end_ts, "time"):
                    window_seconds = max(0, int(end_ts.time) - int(start_ts.time))
                    metrics["oplog_window_seconds"] = window_seconds
        except Exception as e:
            logger.warning(f"Could not get oplog window from {host}: {e}")

        settings = ((rs_config or {}).get("config") or {}).get("settings") or {}
        election_timeout = settings.get("electionTimeoutMillis")
        heartbeat_interval = settings.get("heartbeatIntervalMillis")
        wc_majority = settings.get("writeConcernMajorityJournalDefault")
        if isinstance(election_timeout, int):
            metrics["election_timeout_millis"] = election_timeout
        if isinstance(heartbeat_interval, int):
            metrics["heartbeat_interval_millis"] = heartbeat_interval
        if isinstance(wc_majority, bool):
            metrics["write_concern_majority_journal_default"] = wc_majority

        return metrics

    def collect_cluster_data(self, cluster_name: str) -> Optional[Dict]:
        """Collect all data for a cluster"""
        from app.config import get_app_config
        current_config = get_app_config()
        if not current_config:
            logger.error("App config not initialized")
            return None

        cluster_config = current_config.get_cluster_by_name(cluster_name)
        if not cluster_config:
            logger.error(f"Cluster {cluster_name} not found in config")
            return None

        try:
            primary_client, connected_host, topology, connected_credentials = self._connect_for_cluster(cluster_config)
            if not primary_client or not connected_host or not topology or not connected_credentials:
                logger.error(f"Could not connect to any node in cluster {cluster_name}")
                return None
            connected_username, connected_password = connected_credentials

            if topology["mode"] == "standalone":
                try:
                    databases = self._collect_databases(primary_client)
                    users = self._collect_users(primary_client)
                    feature_compatibility_version = self._collect_feature_compatibility_version(primary_client, connected_host)
                    node_data = self._build_standalone_node_data(
                        connected_host,
                        primary_client,
                        connected_username,
                        connected_password,
                        current_config.exclude_databases if current_config else ['admin', 'config', 'local'],
                    )
                    nodes_data = enrich_node_resources([node_data], cluster_config)
                    return {
                        'name': cluster_name,
                        'nodes': nodes_data,
                        'databases': databases,
                        'feature_compatibility_version': feature_compatibility_version,
                        'users': users,
                    }
                finally:
                    primary_client.close()

            rs_status = topology["rs_status"]
            rs_config = topology["rs_config"]

            # For lag calculation we need PRIMARY perspective, otherwise secondary node can
            # report slightly stale member optimes and produce false-positive lag.
            primary_host_name = None
            if primary_client:
                for member in rs_status.get('members', []):
                    if member.get('self', False) and member.get('stateStr') == 'PRIMARY':
                        primary_host_name = member.get('name')
                        break

            if not primary_host_name:
                primary_member = next(
                    (m for m in rs_status.get('members', []) if m.get('stateStr') == 'PRIMARY'),
                    None
                )
                primary_host = primary_member.get('name') if primary_member else None
                if primary_host:
                    primary_host = self._normalize_host(primary_host)
                    try:
                        primary_client_for_status = self._build_client(
                            primary_host,
                            cluster_config.username,
                            cluster_config.password,
                        )
                        primary_client_for_status.admin.command('ping')
                        rs_status = primary_client_for_status.admin.command("replSetGetStatus")
                        rs_config = primary_client_for_status.admin.command("replSetGetConfig")
                        connected_host = primary_host
                        primary_client = primary_client_for_status
                        primary_host_name = primary_member.get('name')
                        log_collector_progress(
                            logger,
                            "mongodb",
                            cluster_name,
                            "primary_selected_for_lag",
                            primary_host=primary_host,
                        )
                    except Exception as e:
                        logger.warning(
                            "Could not refresh replSet status from PRIMARY %s, using fallback status: %s",
                            primary_host,
                            e,
                        )

            # Collect data for all nodes from replica set status
            nodes_data = []
            primary_node = None
            primary_election_date_iso = None
            feature_compatibility_version = None
            primary_list_databases = None
            primary_mongo_version = None

            # Build a map of member IDs to configs
            member_configs = {cfg['_id']: cfg for cfg in rs_config['config']['members']}

            # Find PRIMARY optime for lag calculation
            primary_optime = None
            for member in rs_status['members']:
                if member['stateStr'] == 'PRIMARY':
                    primary_optime = member.get('optimeDate')
                    primary_host_name = member.get('name')
                    break

            # Collect cluster-level data from PRIMARY once and reuse.
            if primary_client and primary_host_name:
                try:
                    primary_host_for_meta = primary_host_name if ':' in primary_host_name else f"{primary_host_name}:27017"
                    _log_mongo_request(primary_host_for_meta, "listDatabases")
                    primary_list_databases = primary_client.admin.command("listDatabases")
                    _log_mongo_request(primary_host_for_meta, "buildInfo")
                    build_info = primary_client.admin.command("buildInfo")
                    version = build_info.get("version")
                    primary_mongo_version = str(version) if version else None
                    self._set_cached_version(primary_host_name, primary_mongo_version)
                except Exception as e:
                    logger.warning(f"Could not collect primary metadata for {primary_host_name}: {e}")

            # Collect data for each member in replica set
            for member in rs_status['members']:
                member_id = member.get('_id')
                member_config = member_configs.get(member_id, {})

                # Calculate lag (clamp to 0: replica can appear ahead due to clock skew or race)
                lag_seconds = 0
                if primary_optime and member.get('optimeDate'):
                    lag = primary_optime - member['optimeDate']
                    lag_seconds = max(0, int(lag.total_seconds()))

                # Get size information. Cluster-level data stays on PRIMARY,
                # per-node details are fetched separately only where needed.
                member_host = member['name']
                if primary_list_databases is not None and member_host == primary_host_name:
                    total_size = primary_list_databases.get('totalSize', 0)
                    local_db_size = 0
                    for db in primary_list_databases.get('databases', []):
                        db_name = db.get('name')
                        if db_name in (current_config.exclude_databases if current_config else ['admin', 'config', 'local']):
                            total_size -= db.get('sizeOnDisk', 0)
                        if db_name == 'local':
                            local_db_size = db.get('sizeOnDisk', 0)
                    oplog_size = 0
                    try:
                        primary_host_for_metrics = member_host if ':' in member_host else f"{member_host}:27017"
                        _log_mongo_request(
                            primary_host_for_metrics, "collStats", database="local", collection="oplog.rs"
                        )
                        oplog_stats = primary_client["local"].command("collStats", "oplog.rs")
                        oplog_size = int(oplog_stats.get("storageSize") or oplog_stats.get("size") or 0)
                    except Exception as e:
                        logger.warning(f"Could not get primary oplog collStats for node {member_host}: {e}")
                        oplog_size = int(local_db_size or 0)
                    size_info = {
                        'oplog': oplog_size,
                        'data': max(0, total_size),
                    }
                    self._set_cached_size(member_host, size_info)
                else:
                    size_info = self._collect_size_for_host(
                        host=member_host,
                        username=cluster_config.username,
                        password=cluster_config.password,
                        exclude_databases=current_config.exclude_databases if current_config else ['admin', 'config', 'local'],
                    )

                health = member.get('health', 1)
                node_data = {
                    'name': member['name'],
                    'stateStr': member['stateStr'],
                    'status': 'UP' if health == 1 else 'DOWN',
                    'lag': lag_seconds,
                    'priority': int(member_config.get('priority', 0)),
                    'hidden': int(member_config.get('hidden', 0)),
                    'uptime': member.get('uptime'),
                    'sync_source_host': member.get('syncSourceHost'),
                    'mongo_version': (
                        primary_mongo_version
                        if member_host == primary_host_name and primary_mongo_version is not None
                        else self._collect_mongo_version_for_host(
                            host=member_host,
                            username=cluster_config.username,
                            password=cluster_config.password,
                        )
                    ),
                    'self': member.get('self', False)
                }

                if size_info:
                    node_data['size'] = size_info

                if member['stateStr'] == 'PRIMARY':
                    primary_node = node_data
                    election_date = member.get('electionDate')
                    if election_date and hasattr(election_date, "isoformat"):
                        # PyMongo returns naive UTC datetime; emit with Z so JS parses as UTC.
                        primary_election_date_iso = _utc_isoformat(election_date)
                    else:
                        # Fallback for cases where only electionTime is available.
                        election_time = member.get('electionTime')
                        if election_time and hasattr(election_time, "time"):
                            primary_election_date_iso = _utc_isoformat(
                                datetime.fromtimestamp(election_time.time, tz=timezone.utc)
                            )

                nodes_data.append(node_data)

            # If we don't have PRIMARY client yet, try to connect to PRIMARY node
            if not primary_client:
                for member in rs_status['members']:
                    if member['stateStr'] == 'PRIMARY':
                        try:
                            primary_host = self._normalize_host(member['name'])
                            primary_client = self._build_client(
                                primary_host,
                                cluster_config.username,
                                cluster_config.password,
                            )
                            primary_client.admin.command('ping')
                            primary_host_name = member['name']
                            break
                        except Exception as e:
                            logger.warning(f"Could not connect to PRIMARY node {member['name']}: {e}")
                            continue

            # Collect databases and users from PRIMARY node
            databases = []
            users = []
            primary_metrics = {}

            if primary_client:
                try:
                    primary_host_for_meta = primary_host_name
                    if primary_host_for_meta and ':' not in primary_host_for_meta:
                        primary_host_for_meta = f"{primary_host_for_meta}:27017"
                    if primary_host_for_meta:
                        feature_compatibility_version = self._collect_feature_compatibility_version(
                            primary_client, primary_host_for_meta
                        )
                        primary_metrics = self._collect_primary_metrics(primary_client, primary_host_for_meta, rs_config)
                    databases = self._collect_databases(primary_client, preloaded_list_databases=primary_list_databases)
                    users = self._collect_users(primary_client)
                except Exception as e:
                    logger.error(f"Error collecting databases/users from PRIMARY: {e}")
                finally:
                    primary_client.close()

            # Build primary history and add to PRIMARY node
            primary_history = None
            if primary_node:
                primary_history = {
                    # Use MongoDB election timestamp when available.
                    'became_primary_at': primary_election_date_iso,
                    'previous_primary': None  # TODO: track previous PRIMARY
                }
                # Add primary_history to PRIMARY node
                for node_data in nodes_data:
                    if node_data.get('stateStr') == 'PRIMARY':
                        node_data['primary_history'] = primary_history
                        if primary_metrics:
                            node_data['primary_metrics'] = primary_metrics
                        break

            nodes_data = enrich_node_resources(nodes_data, cluster_config)

            return {
                'name': cluster_name,
                'nodes': nodes_data,
                'databases': databases,
                'feature_compatibility_version': feature_compatibility_version,
                'users': users,
            }

        except Exception as e:
            logger.error(f"Error collecting data for cluster {cluster_name}: {e}")
            return None

    def _collect_node_data(self, node_config, username: Optional[str] = None, password: Optional[str] = None) -> Optional[Dict]:
        """Collect data from a single MongoDB node"""
        try:
            host, node_username, node_password = self._extract_node_connection_params(node_config, username, password)
            client = self._build_client(host, node_username, node_password)
            _log_mongo_request(host, "ping")
            client.admin.command("ping")
            topology = self._detect_topology(client, host)

            if topology["mode"] == "standalone":
                node_data = self._build_standalone_node_data(
                    host,
                    client,
                    node_username,
                    node_password,
                    ['admin', 'config', 'local'],
                )
                client.close()
                return node_data

            rs_status = topology["rs_status"]
            rs_config = topology["rs_config"]

            # Find this node in members
            self_member = None
            for member in rs_status['members']:
                if member.get('self', False):
                    self_member = member
                    break

            if not self_member:
                return None

            # Find member config
            member_config = None
            for member_cfg in rs_config['config']['members']:
                if member_cfg['_id'] == self_member['_id']:
                    member_config = member_cfg
                    break

            # Calculate lag
            primary_optime = None
            for member in rs_status['members']:
                if member['stateStr'] == 'PRIMARY':
                    primary_optime = member.get('optimeDate')
                    break

            lag_seconds = 0
            if primary_optime and self_member.get('optimeDate'):
                lag = primary_optime - self_member['optimeDate']
                lag_seconds = max(0, int(lag.total_seconds()))

            health = self_member.get('health', 1)
            # Get size information (only for PRIMARY or self node)
            size_info = None
            if self_member['stateStr'] == 'PRIMARY' or self_member.get('self', False):
                try:
                    _log_mongo_request(host, "listDatabases")
                    list_databases = client.admin.command("listDatabases")
                    total_size = list_databases.get('totalSize', 0)
                    oplog_size = 0

                    for db in list_databases.get('databases', []):
                        if db['name'] in ['admin', 'config', 'local']:
                            total_size -= db.get('sizeOnDisk', 0)
                        if db['name'] == 'local':
                            oplog_size = db.get('sizeOnDisk', 0)

                    size_info = {
                        'oplog': oplog_size,
                        'data': max(0, total_size)
                    }
                except Exception as e:
                    logger.warning(f"Could not get size info: {e}")

            node_data = {
                'name': self_member['name'],
                'stateStr': self_member['stateStr'],
                'status': 'UP' if health == 1 else 'DOWN',
                'lag': lag_seconds,
                'priority': int(member_config.get('priority', 0)) if member_config else 0,
                'hidden': int(member_config.get('hidden', 0)) if member_config else 0,
                'uptime': self_member.get('uptime'),
                'sync_source_host': self_member.get('syncSourceHost'),  # Map to snake_case for Pydantic
                'self': self_member.get('self', False)
            }

            if size_info:
                node_data['size'] = size_info

            client.close()
            return node_data

        except ServerSelectionTimeoutError:
            host_str = node_config.get('host') if isinstance(node_config, dict) else str(node_config)
            logger.warning(f"Timeout connecting to node {host_str}")
            return None
        except Exception as e:
            host_str = node_config.get('host') if isinstance(node_config, dict) else str(node_config)
            logger.error(f"Error collecting data from node {host_str}: {e}")
            return None

    def _collect_databases(self, client: MongoClient, preloaded_list_databases: Optional[Dict] = None) -> List[str]:
        """Collect list of databases (excluding configured databases)"""
        from app.config import get_app_config
        current_config = get_app_config()
        exclude_list = current_config.exclude_databases if current_config else ['admin', 'config', 'local']

        try:
            list_databases = preloaded_list_databases
            if list_databases is None:
                # Get host from client for logging
                host = str(client.address[0]) if hasattr(client, 'address') and client.address else "unknown"
                if hasattr(client, 'address') and len(client.address) > 1:
                    host = f"{host}:{client.address[1]}"
                _log_mongo_request(host, "listDatabases")
                list_databases = client.admin.command("listDatabases")
            databases = []
            for db in list_databases.get('databases', []):
                db_name = db['name']
                if db_name not in exclude_list:
                    databases.append(db_name)
            return sorted(databases)
        except Exception as e:
            logger.error(f"Error collecting databases: {e}")
            return []

    def _collect_users(self, client: MongoClient) -> List[Dict]:
        """Collect users from MongoDB (from PRIMARY node)"""
        try:
            # Get host from client for logging
            host = str(client.address[0]) if hasattr(client, 'address') and client.address else "unknown"
            if hasattr(client, 'address') and len(client.address) > 1:
                host = f"{host}:{client.address[1]}"
            _log_mongo_request(host, "find", database="admin", collection="system.users", filter={})
            users_dict = defaultdict(lambda: defaultdict(list))

            # Add timeout for users query (max 10 seconds)
            for user_doc in client.admin.system.users.find({}).max_time_ms(10000):
                username = user_doc.get('user')
                for role in user_doc.get('roles', []):
                    db_name = role.get('db', 'admin')
                    role_name = role.get('role', '')
                    users_dict[username][db_name].append(role_name)

            users_list = []
            for username, db_data in users_dict.items():
                for db_name, roles in db_data.items():
                    users_list.append({
                        'user': username,
                        'db': db_name,
                        'roles': ','.join(roles)
                    })

            return users_list
        except Exception as e:
            logger.error(f"Error collecting users: {e}")
            return []

    def collect_collections(self, cluster_name: str, database_name: str) -> List[Dict]:
        """Collect collections for a specific database"""
        from app.config import get_app_config
        current_config = get_app_config()
        if not current_config:
            return []

        cluster_config = current_config.get_cluster_by_name(cluster_name)
        if not cluster_config:
            return []

        read_host, topology_mode, read_username, read_password = self._resolve_read_host(cluster_config)
        if not read_host:
            logger.error(f"Could not find readable node for cluster {cluster_name}")
            return []

        # Connect to readable node and collect collections.
        try:
            primary_client = self._build_client(
                read_host,
                read_username,
                read_password,
            )
            _log_mongo_request(read_host, "ping")
            primary_client.admin.command('ping')

            # Get collections
            from app.config import get_app_config
            current_config = get_app_config()
            exclude_collections = current_config.exclude_collections if current_config else []

            db = primary_client[database_name]
            _log_mongo_request(read_host, "listCollections", database=database_name)
            collections = []
            collection_names = db.list_collection_names()
            # Limit collections to prevent timeout on databases with many collections
            max_collections = 200
            if len(collection_names) > max_collections:
                logger.warning(f"Database {database_name} has {len(collection_names)} collections, limiting to {max_collections}")
                collection_names = collection_names[:max_collections]

            for collection_name in collection_names:
                _log_mongo_request(read_host, "collStats", database=database_name, collection=collection_name)
                # Skip excluded collections
                if collection_name in exclude_collections:
                    continue

                try:
                    # Add timeout for collStats (max 10 seconds per collection)
                    stats = db.command("collStats", collection_name, maxTimeMS=10000)
                    indexes_count = None
                    try:
                        # Fast metadata lookup to render indexes count without opening details.
                        indexes_count = len(db[collection_name].index_information())
                    except Exception as idx_error:
                        logger.warning(f"Could not get index count for collection {collection_name}: {idx_error}")
                    collections.append({
                        'name': collection_name,
                        'count': stats.get('count', 0),
                        'size': stats.get('size', 0),
                        'indexes_count': indexes_count,
                    })
                except Exception as e:
                    logger.warning(f"Could not get stats for collection {collection_name}: {e}")
                    collections.append({
                        'name': collection_name,
                        'count': None,
                        'size': None,
                        'indexes_count': None,
                    })

            primary_client.close()
            return collections
        except Exception as e:
            logger.error(f"Error collecting collections from {topology_mode or 'unknown'} node {read_host}: {e}")
            return []

    def collect_collection_indexes(self, cluster_name: str, database_name: str, collection_name: str) -> List[Dict]:
        """Collect index metadata for a specific collection from PRIMARY node."""
        from app.config import get_app_config
        current_config = get_app_config()
        if not current_config:
            return []

        cluster_config = current_config.get_cluster_by_name(cluster_name)
        if not cluster_config:
            return []

        read_host, topology_mode, read_username, read_password = self._resolve_read_host(cluster_config)
        if not read_host:
            logger.error(f"Could not find readable node for cluster {cluster_name}")
            return []

        try:
            primary_client = self._build_client(
                read_host,
                read_username,
                read_password,
            )
            _log_mongo_request(read_host, "ping")
            primary_client.admin.command('ping')

            db = primary_client[database_name]
            collection = db[collection_name]
            _log_mongo_request(read_host, "listIndexes", database=database_name, collection=collection_name)

            indexes: List[Dict] = []
            for index_doc in collection.list_indexes():
                key_doc = index_doc.get('key', {})
                key_parts = [f"{field}:{direction}" for field, direction in key_doc.items()]
                indexes.append({
                    'name': index_doc.get('name', 'unknown'),
                    'key': ", ".join(key_parts),
                    'unique': bool(index_doc.get('unique', False)),
                    'sparse': bool(index_doc.get('sparse', False)),
                    'hidden': bool(index_doc.get('hidden', False)),
                    'ttl_seconds': index_doc.get('expireAfterSeconds'),
                    'partial': bool(index_doc.get('partialFilterExpression')),
                })

            primary_client.close()
            return sorted(indexes, key=lambda idx: idx.get('name', ''))
        except Exception as e:
            logger.error(
                f"Error collecting indexes from {cluster_name}.{database_name}.{collection_name} via {topology_mode or 'unknown'} node {read_host}: {e}"
            )
            return []
