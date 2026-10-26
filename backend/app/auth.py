"""
Authentication and authorization
"""
import logging
import ssl
import bcrypt
import jwt
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
from fastapi import HTTPException, status
from ldap3 import NONE, SUBTREE, Connection, Server, Tls
from ldap3.core.exceptions import LDAPException
from ldap3.utils.conv import escape_filter_chars

from app.authorization import build_user_payload
from app.config import get_app_config, settings

logger = logging.getLogger(__name__)


def _log_auth_success(user: Dict[str, Any]) -> None:
    logger.info(
        "Authentication succeeded",
        extra={
            "auth_provider": user.get("auth_provider"),
            "username": user.get("username") or user.get("sub"),
            "roles": user.get("roles", []),
            "permissions_count": len(user.get("permissions", [])),
        },
    )


def _log_auth_failure(username: str, auth_provider: str, reason: str = "invalid_credentials") -> None:
    logger.warning(
        "Authentication failed",
        extra={
            "auth_provider": auth_provider,
            "username": username,
            "reason": reason,
        },
    )


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify password against bcrypt hash"""
    try:
        return bcrypt.checkpw(
            plain_password.encode('utf-8'),
            hashed_password.encode('utf-8')
        )
    except Exception:
        return False


def get_password_hash(password: str) -> str:
    """Generate bcrypt hash for password"""
    return bcrypt.hashpw(
        password.encode('utf-8'),
        bcrypt.gensalt()
    ).decode('utf-8')


def authenticate_user(username: str, password: str) -> Optional[Dict]:
    """Authenticate user via configured provider and return effective user payload."""
    current_config = get_app_config()
    if not current_config:
        return None

    auth_config = getattr(current_config, "auth_config", {}) or {}
    provider = str(auth_config.get("provider") or "local").strip().lower()
    allow_local_fallback = bool(auth_config.get("allow_local_fallback") or auth_config.get("allowLocalFallback"))

    if provider == "freeipa":
        user = _authenticate_freeipa(username, password)
        if user:
            _log_auth_success(user)
            return user
        if allow_local_fallback:
            fallback_user = _authenticate_local(username, password)
            if fallback_user:
                _log_auth_success(fallback_user)
                return fallback_user
        _log_auth_failure(username, "freeipa")
        return None

    user = _authenticate_local(username, password)
    if user:
        _log_auth_success(user)
        return user
    _log_auth_failure(username, "local")
    return None


def _authenticate_local(username: str, password: str) -> Optional[Dict]:
    """Authenticate local emergency/break-glass user from config."""
    current_config = get_app_config()
    if not current_config:
        return None

    for user in current_config.users:
        if user.get('username') == username:
            password_hash = user.get('password_hash')
            if password_hash and verify_password(password, password_hash):
                return build_user_payload(
                    username=username,
                    display_name=user.get("display_name") or username,
                    auth_provider="local",
                    roles=user.get("roles") or ["viewer"],
                )
    return None


def _iter_freeipa_servers(freeipa_config: Dict[str, Any]) -> list[Dict[str, Any]]:
    servers = freeipa_config.get("servers")
    if isinstance(servers, list) and servers:
        return [server for server in servers if isinstance(server, dict)]
    if freeipa_config.get("host"):
        return [freeipa_config]
    return []


def _create_ldap_server(server_config: Dict[str, Any]) -> Server:
    verify_tls = bool(server_config.get("verify_tls", server_config.get("verifyTls", True)))
    tls = Tls(validate=ssl.CERT_REQUIRED if verify_tls else ssl.CERT_NONE)
    connect_timeout = int(server_config.get("connect_timeout_s", server_config.get("connectTimeoutS", server_config.get("timeout_s", server_config.get("timeoutS", 3)))))
    return Server(
        str(server_config.get("host")),
        port=int(server_config.get("port", 389)),
        use_ssl=bool(server_config.get("use_ssl", server_config.get("useSsl", False))),
        get_info=NONE,
        connect_timeout=connect_timeout,
        tls=tls,
    )


def _open_bound_connection(
    server: Server,
    server_config: Dict[str, Any],
    user: str,
    password: str,
) -> Connection:
    receive_timeout = int(server_config.get("timeout_s", server_config.get("timeoutS", 10)))
    connection = Connection(
        server,
        user=user,
        password=password,
        raise_exceptions=True,
        receive_timeout=receive_timeout,
    )
    connection.open()
    if bool(server_config.get("start_tls", server_config.get("startTls", False))):
        connection.start_tls()
    connection.bind()
    return connection


def _normalize_groups(value: Any) -> list[str]:
    values_attr = getattr(value, "values", None)
    if isinstance(values_attr, (list, tuple, set)):
        return [str(item).strip() for item in values_attr if str(item).strip()]

    value_attr = getattr(value, "value", None)
    if isinstance(value_attr, (list, tuple, set)):
        return [str(item).strip() for item in value_attr if str(item).strip()]
    if value_attr is not None:
        text = str(value_attr).strip()
        return [text] if text else []

    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _normalize_attribute_scalar(value: Any) -> Optional[str]:
    if value is None:
        return None

    values_attr = getattr(value, "values", None)
    if isinstance(values_attr, (list, tuple, set)):
        for item in values_attr:
            text = str(item).strip()
            if text:
                return text

    value_attr = getattr(value, "value", None)
    if isinstance(value_attr, (list, tuple, set)):
        for item in value_attr:
            text = str(item).strip()
            if text:
                return text
        return None
    if value_attr is not None:
        text = str(value_attr).strip()
        return text or None

    text = str(value).strip()
    return text or None


def _resolve_nested_group_dns(
    connection: Connection,
    search_bases: list[str],
    *,
    user_dn: str,
    direct_group_dns: list[str],
) -> list[str]:
    resolved: set[str] = set()
    queue: list[str] = []

    for value in direct_group_dns:
        normalized = str(value).strip()
        if normalized and normalized not in resolved:
            resolved.add(normalized)
            queue.append(normalized)

    pending_members: list[str] = [user_dn] + queue.copy()
    processed_members: set[str] = set()

    while pending_members:
        member_dn = str(pending_members.pop(0)).strip()
        if not member_dn or member_dn in processed_members:
            continue
        processed_members.add(member_dn)

        member_filter = f"(member={escape_filter_chars(member_dn)})"
        for search_base in search_bases:
            connection.search(
                search_base=str(search_base),
                search_filter=member_filter,
                search_scope=SUBTREE,
                attributes=[],
            )
            for entry in connection.entries:
                group_dn = str(entry.entry_dn).strip()
                if not group_dn or group_dn in resolved:
                    continue
                resolved.add(group_dn)
                pending_members.append(group_dn)

    return sorted(resolved)


def _get_entry_attribute(entry: Any, attr_name: Optional[str]) -> Any:
    if not attr_name:
        return None

    direct_value = getattr(entry, attr_name, None)
    if direct_value:
        return direct_value

    attr_map = getattr(entry, "entry_attributes_as_dict", None)
    if not isinstance(attr_map, dict):
        return None

    expected = attr_name.lower()
    for key, value in attr_map.items():
        if str(key).lower() == expected:
            return value
    return None


def _build_display_name(entry: Any, attr_name: Optional[str], attr_surname: Optional[str], fallback: str) -> str:
    parts: list[str] = []
    if attr_name:
        value = _normalize_attribute_scalar(_get_entry_attribute(entry, attr_name))
        if value:
            parts.append(value)
    if attr_surname:
        value = _normalize_attribute_scalar(_get_entry_attribute(entry, attr_surname))
        if value:
            parts.append(value)
    return " ".join(part for part in parts if part).strip() or fallback


def _authenticate_freeipa(username: str, password: str) -> Optional[Dict]:
    current_config = get_app_config()
    if not current_config:
        return None

    auth_config = getattr(current_config, "auth_config", {}) or {}
    freeipa_config = auth_config.get("freeipa") if isinstance(auth_config, dict) else None
    if not isinstance(freeipa_config, dict):
        return None

    search_filter_template = str(freeipa_config.get("search_filter") or freeipa_config.get("user_filter") or "(uid={username})")
    attributes_config = freeipa_config.get("attributes") if isinstance(freeipa_config.get("attributes"), dict) else {}
    username_attr = str(attributes_config.get("username") or freeipa_config.get("username_attr") or "uid")
    member_of_attr = str(attributes_config.get("member_of") or freeipa_config.get("group_member_attr") or "memberOf")
    name_attr = str(attributes_config.get("name") or freeipa_config.get("display_name_attr") or "givenName")
    surname_attr = str(attributes_config.get("surname") or freeipa_config.get("surname_attr") or "sn")
    email_attr = str(attributes_config.get("email") or freeipa_config.get("email_attr") or "mail")
    bind_dn = str(freeipa_config.get("bind_dn") or freeipa_config.get("bind_user_dn") or "").strip()
    bind_password = (
        freeipa_config.get("bind_password")
        or settings.freeipa_bind_password
    )
    user_search_bases = freeipa_config.get("search_base_dns") or freeipa_config.get("searchBaseDns") or []
    if not isinstance(user_search_bases, list):
        user_search_bases = [user_search_bases] if user_search_bases else []
    if not user_search_bases:
        logger.warning("FreeIPA auth skipped because no search bases are configured")
        return None
    group_search_bases = (
        freeipa_config.get("group_search_base_dns")
        or freeipa_config.get("groupSearchBaseDns")
        or user_search_bases
    )
    if not isinstance(group_search_bases, list):
        group_search_bases = [group_search_bases] if group_search_bases else []

    escaped_username = escape_filter_chars(username)
    search_filter = search_filter_template.replace("{username}", escaped_username).replace("%s", escaped_username)

    for server_config in _iter_freeipa_servers(freeipa_config):
        reader_connection: Connection | None = None
        user_connection: Connection | None = None
        try:
            server = _create_ldap_server(server_config)
            reader_connection = _open_bound_connection(server, server_config, bind_dn, str(bind_password or ""))
            user_entry = None
            for search_base in user_search_bases:
                reader_connection.search(
                    search_base=str(search_base),
                    search_filter=search_filter,
                    search_scope=SUBTREE,
                    attributes=[username_attr, member_of_attr, name_attr, surname_attr, email_attr],
                )
                if reader_connection.entries:
                    user_entry = reader_connection.entries[0]
                    break

            if user_entry is None:
                reader_connection.unbind()
                continue

            user_dn = str(user_entry.entry_dn)
            direct_groups = _normalize_groups(_get_entry_attribute(user_entry, member_of_attr))
            groups = _resolve_nested_group_dns(
                reader_connection,
                [str(base) for base in group_search_bases],
                user_dn=user_dn,
                direct_group_dns=direct_groups,
            )

            user_connection = _open_bound_connection(server, server_config, user_dn, password)

            display_name = _build_display_name(user_entry, name_attr, surname_attr, username)
            email = _normalize_attribute_scalar(_get_entry_attribute(user_entry, email_attr))
            resolved_username = _normalize_attribute_scalar(_get_entry_attribute(user_entry, username_attr)) or username
            payload = build_user_payload(
                username=resolved_username,
                display_name=display_name,
                email=email,
                groups=groups,
                auth_provider="freeipa",
            )
            return payload
        except LDAPException as exc:
            logger.debug(
                "FreeIPA authentication attempt failed",
                extra={"auth_provider": "freeipa", "username": username, "error_type": exc.__class__.__name__},
            )
            continue
        except Exception as exc:
            logger.warning(
                "FreeIPA authentication backend error",
                extra={"auth_provider": "freeipa", "username": username, "error_type": exc.__class__.__name__},
            )
            continue
        finally:
            if user_connection is not None and user_connection.bound:
                user_connection.unbind()
            if reader_connection is not None and reader_connection.bound:
                reader_connection.unbind()
    return None


def create_access_token(data: Dict) -> str:
    """Create JWT access token"""
    to_encode = {
        "sub": data.get("sub") or data.get("username"),
        "username": data.get("username") or data.get("sub"),
        "display_name": data.get("display_name"),
        "auth_provider": data.get("auth_provider"),
        "groups": data.get("groups", []) if data.get("auth_provider") == "authentik" else [],
        "roles": data.get("roles", []),
        "permissions": data.get("permissions", []),
    }
    expire = datetime.now(timezone.utc) + timedelta(hours=settings.jwt_expiration_hours)
    to_encode.update({"exp": expire})

    encoded_jwt = jwt.encode(
        to_encode,
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm
    )
    return encoded_jwt


def verify_token(token: str) -> Optional[Dict]:
    """Verify JWT token and return payload"""
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm]
        )
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired"
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token"
        )
