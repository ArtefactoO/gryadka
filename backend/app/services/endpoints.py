"""Shared host/port parsing for database collectors and operations."""
from urllib.parse import urlsplit


def parse_host_port(node, default_port: int) -> tuple[str, int]:
    raw = str(node.get('host', '') if isinstance(node, dict) else node).strip()
    explicit = node.get('port') if isinstance(node, dict) else None
    if not raw:
        return '', default_port
    parsed = urlsplit('//'+raw)
    if parsed.username or parsed.password or parsed.path or not parsed.hostname:
        raise ValueError('Endpoint must contain a host and optional port, without credentials')
    port = int(explicit if explicit is not None else parsed.port or default_port)
    if not 1 <= port <= 65535:
        raise ValueError('Endpoint port is outside the valid range')
    return parsed.hostname, port
