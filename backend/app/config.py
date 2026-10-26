"""
Configuration loading from config.yaml and environment variables.
Credentials: if env MONITOR_<MONITOR>_<TARGET>_USERNAME / _PASSWORD are set, use them;
else use username/password from config; if both empty, no auth (e.g. Redpanda).
"""
import os
import logging
import yaml
import bcrypt
from pathlib import Path
from typing import Dict, List, Optional, Any
from pydantic_settings import BaseSettings
from pydantic import Field


def reject_inline_secrets(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in {'password', 'password_hash', 'client_secret', 'jwt_secret', 'token', 'private_key', 'bind_password', 'basic_auth', 'basicauth', 'saslpassword', 'sasl_plain_password'} and item:
                raise ValueError('Runtime credentials must come from Secret environment variables')
            reject_inline_secrets(item)
    elif isinstance(value, list):
        for item in value:
            reject_inline_secrets(item)


class Settings(BaseSettings):
    """Application settings from environment variables"""
    redis_host: str = "redis"
    redis_port: int = 6379
    redis_password: Optional[str] = None
    redis_db: int = 0
    redis_cache_db: Optional[int] = None
    redis_mongodb_db: Optional[int] = None
    redis_system_db: Optional[int] = None
    redis_celery_db: Optional[int] = None
    redis_rabbitmq_db: Optional[int] = None
    redis_redis_db: Optional[int] = None
    redis_haproxy_db: Optional[int] = None
    redis_rollouts_db: Optional[int] = None
    redis_logging_db: Optional[int] = None
    redis_emqx_db: Optional[int] = None
    redis_postgresql_db: Optional[int] = None
    redis_kafka_db: Optional[int] = None
    public_base_url: str = "http://localhost:8082"
    redis_socket_connect_timeout_s: float = 3.0
    redis_socket_timeout_s: float = 5.0
    redis_health_check_interval_s: int = 30
    redis_retry_on_timeout: bool = True
    redis_retry_attempts: int = 3
    redis_retry_base_delay_s: float = 0.2
    redis_retry_cap_delay_s: float = 2.0
    celery_queue_max_length: int = 15
    celery_broker_visibility_timeout_s: int = 900
    celery_broker_max_connections: int = 20
    celery_task_expires_multiplier: int = 3
    jwt_secret: str = Field(min_length=32)
    jwt_algorithm: str = "HS256"
    jwt_expiration_hours: int = 1
    freeipa_bind_password: Optional[str] = None
    oidc_client_id: Optional[str] = None
    oidc_client_secret: Optional[str] = None
    oidc_allow_insecure_local: bool = False
    update_interval: int = 15
    postgres_host: Optional[str] = None
    postgres_port: int = 5432
    postgres_db: Optional[str] = None
    postgres_user: Optional[str] = None
    postgres_password: Optional[str] = None
    gitlab_username: Optional[str] = None
    gitlab_token: Optional[str] = None
    mongo_backups_token: Optional[str] = None
    repo_cache_dir: str = "/var/lib/gryadka/repos"
    log_level: str = "INFO"
    slow_request_log_threshold_ms: int = 2000
    readiness_slow_log_threshold_ms: int = 500
    event_loop_lag_log_threshold_ms: int = 1000
    event_loop_lag_check_interval_s: float = 1.0
    config_path: str = "/app/config.yaml"

    class Config:
        env_file = ".env"
        env_prefix = ""
        case_sensitive = False


class ClusterConfig:
    """Configuration for a MongoDB cluster"""
    def __init__(self, name: str, nodes: List[Dict[str, str]], username: Optional[str] = None, password: Optional[str] = None, infrastructure: Optional[Dict[str, Any]] = None):
        self.name = name
        self.nodes = nodes
        self.username = username
        self.password = password
        self.infrastructure = infrastructure or {}


class TargetConfig:
    """Configuration for non-Mongo monitoring target"""
    def __init__(
        self,
        name: str,
        nodes: List[Dict[str, str]],
        username: Optional[str] = None,
        password: Optional[str] = None,
        basic_auth: Optional[str] = None,
        options: Optional[Dict[str, Any]] = None,
        infrastructure: Optional[Dict[str, Any]] = None,
    ):
        self.name = name
        self.nodes = nodes
        self.username = username
        self.password = password
        self.basic_auth = basic_auth
        self.options = options or {}
        self.infrastructure = infrastructure or {}


class AppConfig:
    """Application configuration loaded from config.yaml"""
    def __init__(self, config_path: str = "/app/config.yaml"):
        self.config_path = config_path
        self.clusters: List[ClusterConfig] = []
        self.users: List[Dict[str, str]] = []
        self.auth_config: Dict[str, Any] = {}
        self.server_selection_timeout_ms: int = 100
        self.update_interval_s: int = 15
        self.exclude_databases: List[str] = ['admin', 'config', 'local']
        self.exclude_collections: List[str] = []
        self.rabbitmq_targets: List[TargetConfig] = []
        self.redis_targets: List[TargetConfig] = []
        self.postgresql_targets: List[TargetConfig] = []
        self.kafka_targets: List[TargetConfig] = []
        self.haproxy_targets: List[TargetConfig] = []
        self.redpanda_targets: List[TargetConfig] = []
        self.elasticsearch_targets: List[TargetConfig] = []
        self.kibana_targets: List[TargetConfig] = []
        self.emqx_targets: List[TargetConfig] = []
        self.monitors: List[Dict[str, Any]] = []
        self.registry_monitor_config: Dict[str, Any] = {}
        self.registry_refresh_interval_s: int = 1200
        self.registry_exclude_private_ips: bool = False
        self.registry_exclude_internal_load_balancers: bool = False
        self.registry_exclude_ip_cidrs: List[str] = []
        self.registry_include_ip_cidrs: List[str] = []
        self.global_config: Dict[str, Any] = {}
        self.victoria_metrics_config: Dict[str, Any] = {}
        self.mongo_backups_config: Dict[str, Any] = {}
        self._load_config()

    def _load_targets_section(
        self, section_data: Any, monitor_name: str
    ) -> List[TargetConfig]:
        """Load generic targets section (rabbitmq/redis/haproxy). Resolve credentials from env or config."""
        result: List[TargetConfig] = []
        if not isinstance(section_data, list):
            return result

        for target_data in section_data:
            if not isinstance(target_data, dict):
                continue
            name = target_data.get('name')
            if not name:
                continue

            username, password = self._resolve_credentials(
                monitor_name, name,
                target_data.get('username'),
                target_data.get('password'),
            )
            basic_auth = self._resolve_basic_auth(
                monitor_name, name,
                target_data.get('basicAuth') or target_data.get('basic_auth'),
            )

            # If 'options' key exists, use it directly; otherwise build from other keys
            if 'options' in target_data and isinstance(target_data['options'], dict):
                options = dict(target_data['options'])
            else:
                options = {
                    key: value
                    for key, value in target_data.items()
                    if key not in {'name', 'nodes', 'username', 'password', 'basicAuth', 'basic_auth', 'infrastructure'}
                }
            # Ensure ui_url from top level is always in options (for Rabbit/ES/Redpanda UI link)
            if 'ui_url' in target_data:
                options['ui_url'] = target_data['ui_url']
            # Keep monitoring_url available even when target uses nested "options" block.
            if 'monitoring_url' in target_data:
                options['monitoring_url'] = target_data['monitoring_url']
            result.append(
                TargetConfig(
                    name=name,
                    nodes=target_data.get('nodes', []),
                    username=username,
                    password=password,
                    basic_auth=basic_auth,
                    options=options,
                    infrastructure=target_data.get('infrastructure'),
                )
            )
        return result

    @staticmethod
    def _resolve_credentials(
        monitor_name: str,
        target_name: str,
        config_username: Optional[str],
        config_password: Optional[str],
    ) -> tuple:
        """Resolve username/password: env override (MONITOR_<M>_<T>_USERNAME/_PASSWORD) else config; empty = no auth."""
        if config_password:
            raise ValueError("Target passwords must be provided through Secret environment variables")
        prefix = f"MONITOR_{monitor_name.replace('-', '_').upper()}_{target_name.replace('-', '_').upper()}"
        username = os.environ.get(f"{prefix}_USERNAME") or config_username
        password = os.environ.get(f"{prefix}_PASSWORD") or config_password
        return (username or None, password or None)

    @staticmethod
    def _resolve_basic_auth(
        monitor_name: str,
        target_name: str,
        config_basic_auth: Optional[str],
    ) -> Optional[str]:
        """Resolve raw Basic auth pair from env/config. Value format: username:password or key:secret."""
        if config_basic_auth:
            raise ValueError("Target basic auth must be provided through Secret environment variables")
        prefix = f"MONITOR_{monitor_name.replace('-', '_').upper()}_{target_name.replace('-', '_').upper()}"
        return os.environ.get(f"{prefix}_BASIC_AUTH") or config_basic_auth or None

    def _load_config(self):
        """Load configuration from YAML file"""
        config_file = Path(self.config_path)

        if not config_file.exists():
            raise FileNotFoundError(f"Config file not found: {self.config_path}")

        with open(config_file, 'r') as f:
            config_data = yaml.safe_load(f) or {}
        reject_inline_secrets(config_data)

        raw_global_config = config_data.get('global')
        self.global_config = dict(raw_global_config) if isinstance(raw_global_config, dict) else {}

        raw_auth_config = config_data.get('auth')
        self.auth_config = dict(raw_auth_config) if isinstance(raw_auth_config, dict) else {}

        # Load MongoDB targets from new section first, then fallback to legacy "clusters".
        # mongodb can be: a list of clusters (legacy), or a dict with targets + optional excludeDatabases/excludeCollections.
        mongodb_section = config_data.get('mongodb')
        if mongodb_section is None:
            mongodb_section = config_data.get('clusters', [])
        if isinstance(mongodb_section, dict):
            mongodb_targets = mongodb_section.get('targets', mongodb_section.get('clusters', []))
            if 'excludeDatabases' in mongodb_section:
                self.exclude_databases = mongodb_section['excludeDatabases']
            if 'excludeCollections' in mongodb_section:
                self.exclude_collections = mongodb_section['excludeCollections']
        else:
            mongodb_targets = mongodb_section

        for cluster_data in mongodb_targets:
            name = cluster_data['name']
            username, password = self._resolve_credentials(
                "mongodb", name,
                cluster_data.get('username'),
                cluster_data.get('password'),
            )
            cluster = ClusterConfig(
                name=name,
                nodes=cluster_data.get('nodes', []),
                username=username,
                password=password,
                infrastructure=cluster_data.get('infrastructure'),
            )
            self.clusters.append(cluster)

        # Load other provider targets for type-driven API/frontend.
        self.rabbitmq_targets = self._load_targets_section(config_data.get('rabbitmq', []), "rabbitmq")
        self.redis_targets = self._load_targets_section(config_data.get('redis', []), "redis")
        self.postgresql_targets = self._load_targets_section(config_data.get('postgresql', []), "postgresql")
        self.kafka_targets = self._load_targets_section(config_data.get('kafka', []), "kafka")
        self.haproxy_targets = self._load_targets_section(config_data.get('haproxy', []), "haproxy")
        self.redpanda_targets = self._load_targets_section(config_data.get('redpanda', []), "redpanda")
        self.elasticsearch_targets = self._load_targets_section(config_data.get('elasticsearch', []), "elasticsearch")
        self.kibana_targets = self._load_targets_section(config_data.get('kibana', []), "kibana")
        self.emqx_targets = self._load_targets_section(config_data.get('emqx', []), "emqx")

        # Load monitors list from config (both 'monitors' array and individual monitor sections)
        self.monitors = config_data.get('monitors', [])

        # Also check for individual monitor sections (Helm chart format)
        config_keys = list(config_data.keys())
        logger.debug("Available config sections: %s", config_keys)
        for monitor_name in ['rollouts', 'argo-rollout', 'argo_rollout', 'addresses', 'registry']:
            if monitor_name in config_keys:
                monitor_data = config_data[monitor_name]
                logger.debug("Found monitor %s in config", monitor_name)
                if isinstance(monitor_data, dict):
                    # Normalize name to 'rollouts' for new structure
                    if monitor_name == 'rollouts':
                        normalized_name = 'rollouts'
                    elif monitor_name in {'addresses', 'registry'}:
                        normalized_name = 'addresses'
                    else:
                        normalized_name = monitor_name
                    self.monitors.append({
                        'name': normalized_name,
                        'config': monitor_data
                    })
                    logger.debug("Added monitor %s with config", normalized_name)
                    if normalized_name == 'addresses':
                        self.registry_monitor_config = dict(monitor_data)
                        refresh_interval = self.registry_monitor_config.get('refreshIntervalS')
                        exclude_private_ips = self.registry_monitor_config.get('excludePrivateIps')
                        exclude_internal_lbs = self.registry_monitor_config.get('excludeInternalLoadBalancers')
                        exclude_ip_cidrs = self.registry_monitor_config.get('excludeIpCidrs')
                        include_ip_cidrs = self.registry_monitor_config.get('includeIpCidrs')
                        try:
                            if refresh_interval is not None:
                                self.registry_refresh_interval_s = max(1, int(refresh_interval))
                        except (TypeError, ValueError):
                            logger.warning(
                                "Invalid registry refreshIntervalS value %r, using default %s",
                                refresh_interval,
                                self.registry_refresh_interval_s,
                            )
                        self.registry_exclude_private_ips = bool(exclude_private_ips)
                        self.registry_exclude_internal_load_balancers = bool(exclude_internal_lbs)
                        if isinstance(exclude_ip_cidrs, list):
                            self.registry_exclude_ip_cidrs = [str(item).strip() for item in exclude_ip_cidrs if str(item).strip()]
                        if isinstance(include_ip_cidrs, list):
                            self.registry_include_ip_cidrs = [str(item).strip() for item in include_ip_cidrs if str(item).strip()]
                elif isinstance(monitor_data, list):
                    # Handle case where monitor data is a list of targets
                    if monitor_name == 'rollouts':
                        normalized_name = 'rollouts'
                    elif monitor_name in {'addresses', 'registry'}:
                        normalized_name = 'addresses'
                    else:
                        normalized_name = monitor_name
                    self.monitors.append({
                        'name': normalized_name,
                        'config': {'targets': monitor_data}
                    })
                    logger.debug("Added monitor %s with targets", normalized_name)

        # Load users for authentication
        if 'users' in config_data:
            loaded_users: List[Dict[str, str]] = []
            for user in config_data['users']:
                if not isinstance(user, dict):
                    continue
                username = user.get('username')
                if not username:
                    continue

                password_hash = user.get('password_hash')
                if not password_hash:
                    env_key = f"AUTH_USER_{username.replace('-', '_').upper()}_PASSWORD"
                    plain_password = os.environ.get(env_key)
                    if plain_password:
                        # Store only hash in runtime config even when plain password comes from Secret env.
                        password_hash = bcrypt.hashpw(
                            plain_password.encode('utf-8'),
                            bcrypt.gensalt()
                        ).decode('utf-8')
                    else:
                        logger.warning(
                            "User %s has no password_hash and env %s is not set; login disabled for this user",
                            username,
                            env_key,
                        )

                loaded_users.append({
                    'username': username,
                    'password_hash': password_hash,
                    'roles': user.get('roles', []),
                    'display_name': user.get('display_name'),
                })
            self.users = loaded_users

        # Load MongoDB settings
        self.server_selection_timeout_ms = config_data.get('serverSelectionTimeoutMS', 100)

        # Load update interval (support both old and new format)
        self.update_interval_s = config_data.get('updateIntervalS') or config_data.get('refreshTimeoutS', 15)

        # Load exclude lists (root level fallback for legacy configs without mongodb as dict)
        if 'excludeDatabases' in config_data:
            self.exclude_databases = config_data['excludeDatabases']
        if 'excludeCollections' in config_data:
            self.exclude_collections = config_data['excludeCollections']

        raw_vm_config = (
            self.global_config.get('victoriaMetrics')
            or self.global_config.get('victoria_metrics')
            or config_data.get('victoriaMetrics')
            or config_data.get('victoria_metrics')
        )
        if isinstance(raw_vm_config, dict):
            self.victoria_metrics_config = dict(raw_vm_config)

        raw_mongo_backups_config = (
            self.global_config.get('mongoBackups')
            or self.global_config.get('mongo_backups')
            or config_data.get('mongoBackups')
            or config_data.get('mongo_backups')
        )
        if isinstance(raw_mongo_backups_config, dict):
            self.mongo_backups_config = dict(raw_mongo_backups_config)

    def get_cluster_by_name(self, name: str) -> Optional[ClusterConfig]:
        """Get cluster configuration by name"""
        for cluster in self.clusters:
            if cluster.name == name:
                return cluster
        return None

    def get_targets_by_type(self, target_type: str) -> List[TargetConfig]:
        """Return configured targets by type."""
        if target_type == 'mongodb':
            return [
                TargetConfig(
                    name=cluster.name,
                    nodes=cluster.nodes,
                    username=cluster.username,
                    password=cluster.password,
                )
                for cluster in self.clusters
            ]
        if target_type == 'rabbitmq':
            return self.rabbitmq_targets
        if target_type == 'redis':
            return self.redis_targets
        if target_type == 'kafka':
            return self.kafka_targets
        if target_type == 'postgresql':
            return self.postgresql_targets
        if target_type == 'haproxy':
            return self.haproxy_targets
        if target_type == 'redpanda':
            return self.redpanda_targets
        if target_type == 'elasticsearch':
            return self.elasticsearch_targets
        if target_type == 'kibana':
            return self.kibana_targets
        if target_type == 'emqx':
            return self.emqx_targets
        if target_type == 'argo-rollout':
            # Find rollouts monitor and return ALL targets (both argo-rollout and deployment)
            for monitor in self.monitors:
                if monitor.get('name') == 'rollouts':
                    monitor_config = monitor.get('config', {})
                    targets = monitor_config.get('targets', [])

                    # Return all targets with type 'argo-rollout'
                    result = []
                    for target in targets:
                        target_name = target.get('name', '')
                        namespace = target.get('namespace', 'default')
                        max_versions = target.get('max_versions', 4)
                        result.append(TargetConfig(
                            name=target_name,  # 'argo-rollout' or 'deployment'
                            nodes=[{'host': namespace}],
                            options={**target, 'max_versions': max_versions}
                        ))
                    return result
            return []
        if target_type in {'addresses', 'registry'}:
            registry_config = self.get_addresses_monitor_config()
            repos = registry_config.get('repos', [])
            if not isinstance(repos, list) or len(repos) == 0:
                return []
            return [
                TargetConfig(
                    name='addresses',
                    nodes=[],
                    options={
                        'refreshIntervalS': self.registry_refresh_interval_s,
                        'manual': registry_config.get('manual', {}),
                        'repos_count': len(repos),
                    },
                )
            ]
        return []

    def get_enabled_monitor_types(self) -> List[str]:
        """Return monitor types that have at least one configured target."""
        enabled: List[str] = []
        supported = (
            "mongodb",
            "rabbitmq",
            "redis",
            "postgresql",
            "kafka",
            "haproxy",
            "redpanda",
            "elasticsearch",
            "emqx",
            "argo-rollout",
            "addresses",
        )
        for target_type in supported:
            if self.get_targets_by_type(target_type):
                enabled.append(target_type)
        return enabled

    def get_addresses_monitor_config(self) -> Dict[str, Any]:
        """Return raw addresses monitor config."""
        if self.registry_monitor_config:
            return dict(self.registry_monitor_config)
        for monitor in self.monitors:
            if monitor.get('name') == 'addresses' and isinstance(monitor.get('config'), dict):
                return dict(monitor['config'])
        return {}

    def get_victoria_metrics_config(self) -> Dict[str, Any]:
        """Return VictoriaMetrics runtime config."""
        return dict(self.victoria_metrics_config)

    def get_mongo_backups_config(self) -> Dict[str, Any]:
        """Return MongoDB backups exporter runtime config."""
        return dict(self.mongo_backups_config)

    def get_addresses_view_configs(self) -> List[Dict[str, Any]]:
        config = self.get_addresses_monitor_config()
        raw_views = config.get('views')
        normalized_views: List[Dict[str, Any]] = []

        if isinstance(raw_views, dict):
            for view_name, view_config in raw_views.items():
                if not isinstance(view_config, dict):
                    continue
                name = str(view_name).strip().lower()
                if not name:
                    continue
                normalized_views.append(
                    {
                        'name': name,
                        'label': view_config.get('name') or view_config.get('label') or ('External IPs' if name == 'external' else 'Internal IPs'),
                        'includeIpCidrs': list(view_config.get('includeIpCidrs') or []),
                        'excludeIpCidrs': list(view_config.get('excludeIpCidrs') or []),
                        'excludePrivateIps': bool(view_config.get('excludePrivateIps', name == 'external' and self.registry_exclude_private_ips)),
                        'excludeInternalLoadBalancers': bool(
                            view_config.get(
                                'excludeInternalLoadBalancers',
                                name == 'external' and self.registry_exclude_internal_load_balancers,
                            )
                        ),
                    }
                )

        if normalized_views:
            return normalized_views

        return [
            {
                'name': 'external',
                'label': 'External IPs',
                'includeIpCidrs': [],
                'excludeIpCidrs': list(self.registry_exclude_ip_cidrs),
                'excludePrivateIps': True,
                'excludeInternalLoadBalancers': self.registry_exclude_internal_load_balancers,
            },
            {
                'name': 'internal',
                'label': 'Internal IPs',
                'includeIpCidrs': list(self.registry_include_ip_cidrs),
                'excludeIpCidrs': [],
                'excludePrivateIps': False,
                'excludeInternalLoadBalancers': False,
            },
        ]

    def get_registry_monitor_config(self) -> Dict[str, Any]:
        """Backward-compatible alias for addresses monitor config."""
        return self.get_addresses_monitor_config()

    def get_haproxy_target_by_name(self, name: str) -> Optional[TargetConfig]:
        """Get HAProxy target configuration by name."""
        for target in self.haproxy_targets:
            if target.name == name:
                return target
        return None

    def get_redpanda_target_by_name(self, name: str) -> Optional[TargetConfig]:
        """Get Redpanda target configuration by name."""
        for target in self.redpanda_targets:
            if target.name == name:
                return target
        return None

    def get_elasticsearch_target_by_name(self, name: str) -> Optional[TargetConfig]:
        """Get Elasticsearch target configuration by name."""
        for target in self.elasticsearch_targets:
            if target.name == name:
                return target
        return None


# Global settings instance
settings = Settings()

# Global app config instance (will be initialized in main.py)
app_config: Optional[AppConfig] = None

logger = logging.getLogger(__name__)


def get_app_config(force_reload: bool = False) -> Optional[AppConfig]:
    """Get initialized app config for any process (API, Celery worker/beat)."""
    global app_config
    if app_config is None or force_reload:
        try:
            app_config = AppConfig(settings.config_path)
            logger.debug(
                "Configuration loaded: %s clusters, %s users",
                len(app_config.clusters),
                len(app_config.users),
            )
        except Exception as e:
            logger.error("Failed to load configuration from %s: %s", settings.config_path, e)
            app_config = None
    return app_config
