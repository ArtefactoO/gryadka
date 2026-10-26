"""
Authorization helpers and default RBAC model.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Optional, Set

from app.config import get_app_config

PermissionSet = Set[str]

DEFAULT_VIEWER_PERMISSIONS: PermissionSet = {
    "targets.read",
    "mongodb.read",
    "mongodb.collections.read",
    "rabbitmq.read",
    "redis.read",
    "postgresql.read",
    "kafka.read",
    "haproxy.read",
    "redpanda.read",
    "elasticsearch.read",
    "kibana.read",
    "addresses.read",
    "registry.read",
    "argo_rollout.read",
}

DEFAULT_ROLE_PERMISSIONS: Dict[str, PermissionSet] = {
    "viewer": set(DEFAULT_VIEWER_PERMISSIONS),
    "admin": {"*"},
    "postgresql-hba": {"postgresql.read", "postgresql.hba.read"},
    "mongodb-rw": {
        "mongodb.read",
        "mongodb.collections.read",
        "mongodb.users.read",
    },
    "rabbitmq-rw": {
        "rabbitmq.read",
        "rabbitmq.users.read",
        "rabbitmq.messages.purge",
        "rabbitmq.queues.delete",
    },
    "redis-rw": {
        "redis.read",
        "redis.keys.read",
        "redis.keys.delete",
    },
    "haproxy-rw": {
        "haproxy.read",
        "haproxy.restart",
    },
    "addresses-rw": {
        "addresses.read",
        "addresses.write",
        "addresses.collect",
    },
    "registry-rw": {
        "addresses.read",
        "addresses.write",
        "addresses.collect",
        "registry.read",
        "registry.write",
        "registry.collect",
    },
    "argo-rollout-rw": {
        "argo_rollout.read",
        "argo_rollout.promote",
        "argo_rollout.restart",
    },
}


def _normalize_iterable(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    if value is None:
        return []
    text = str(value).strip()
    return [text] if text else []


def _group_aliases(value: str) -> Set[str]:
    text = str(value).strip()
    if not text:
        return set()

    normalized = text.lower()
    aliases: Set[str] = {normalized}
    if "," not in normalized:
        if "=" in normalized:
            key, raw_value = normalized.split("=", 1)
            aliases.add(f"{key.strip()}={raw_value.strip()}")
            aliases.add(raw_value.strip())
        return aliases

    parts = [part.strip() for part in normalized.split(",") if part.strip()]
    if not parts:
        return aliases

    # Match only the full DN and its first RDN alias (for example cn=my-group),
    # not generic suffix components like cn=groups or dc=internal.
    first_rdn = parts[0]
    aliases.add(first_rdn)
    if "=" in first_rdn:
        _, raw_value = first_rdn.split("=", 1)
        aliases.add(raw_value.strip())
    return aliases


def _build_role_permissions_map() -> Dict[str, PermissionSet]:
    role_map = {name: set(permissions) for name, permissions in DEFAULT_ROLE_PERMISSIONS.items()}
    current_config = get_app_config()
    auth_config = getattr(current_config, "auth_config", {}) if current_config else {}
    raw_roles = auth_config.get("roles", {}) if isinstance(auth_config, dict) else {}
    if not isinstance(raw_roles, dict):
        return role_map

    for role_name, role_config in raw_roles.items():
        if not role_name:
            continue
        permissions: PermissionSet = set()
        if isinstance(role_config, dict):
            permissions.update(_normalize_iterable(role_config.get("permissions")))
        else:
            permissions.update(_normalize_iterable(role_config))
        if permissions:
            role_map[str(role_name)] = permissions
    return role_map


def resolve_roles_from_groups(groups: Iterable[str]) -> list[str]:
    current_config = get_app_config()
    auth_config = getattr(current_config, "auth_config", {}) if current_config else {}
    raw_mappings = []
    if isinstance(auth_config, dict):
        raw_mappings = auth_config.get("role_mappings") or auth_config.get("roleMappings") or []

    normalized_groups: Set[str] = set()
    for group in groups:
        normalized_groups.update(_group_aliases(str(group)))
    resolved_roles: Set[str] = set()
    for mapping in raw_mappings:
        if not isinstance(mapping, dict):
            continue
        mapping_group_values = (
            _normalize_iterable(mapping.get("group"))
            + _normalize_iterable(mapping.get("group_dn"))
            + _normalize_iterable(mapping.get("freeipa_group"))
        )
        mapping_groups: Set[str] = set()
        for value in mapping_group_values:
            mapping_groups.update(_group_aliases(value))
        if not mapping_groups or normalized_groups.isdisjoint(mapping_groups):
            continue
        resolved_roles.update(_normalize_iterable(mapping.get("roles")))
    return sorted(resolved_roles)


def resolve_permissions(roles: Iterable[str]) -> list[str]:
    role_permissions = _build_role_permissions_map()
    permissions: PermissionSet = set()
    for role in roles:
        permissions.update(role_permissions.get(role, set()))
    return sorted(permissions)


def has_permission(user: Optional[dict], permission: str) -> bool:
    if not user or not isinstance(user, dict):
        return False
    permissions = {
        str(item).strip()
        for item in user.get("permissions", [])
        if str(item).strip()
    }
    return "*" in permissions or permission in permissions


def build_user_payload(
    *,
    username: str,
    auth_provider: str,
    display_name: Optional[str] = None,
    email: Optional[str] = None,
    groups: Optional[Iterable[str]] = None,
    roles: Optional[Iterable[str]] = None,
) -> dict:
    normalized_groups = [group for group in _normalize_iterable(groups)]
    explicit_roles = [role for role in _normalize_iterable(roles)]
    oidc_mode = 'group_mapping'
    if auth_provider == 'authentik':
        config = get_app_config()
        auth_config = getattr(config, 'auth_config', {}) if config else {}
        oidc_mode = (auth_config.get('oidc') or {}).get('access_mode', 'group_mapping')
    if auth_provider == 'authentik' and oidc_mode == 'authenticated_viewer':
        # This mode is a fixed read-only boundary, including for existing sessions.
        effective_roles = ['viewer']
        permissions = sorted(DEFAULT_VIEWER_PERMISSIONS)
    elif auth_provider == 'authentik' and oidc_mode != 'group_mapping':
        effective_roles = []
        permissions = []
    else:
        mapped_roles = resolve_roles_from_groups(normalized_groups)
        effective_roles = sorted(set(explicit_roles) | set(mapped_roles))
        permissions = resolve_permissions(effective_roles)
    return {
        "sub": username,
        "username": username,
        "display_name": display_name or username,
        "email": email,
        "groups": normalized_groups,
        "roles": effective_roles,
        "permissions": permissions,
        "auth_provider": auth_provider,
    }
