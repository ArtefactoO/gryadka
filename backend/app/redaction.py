"""Common credential redaction for structured logs and exception text."""
import os
import re

SENSITIVE_FIELDS = {'password', 'password_hash', 'access_token', 'refresh_token', 'id_token',
                    'token', 'client_secret', 'jwt_secret', 'authorization', 'private_key', 'api_key'}
URI_AUTH = re.compile(r'([a-zA-Z][a-zA-Z0-9+.-]*://)[^/\s]+@')
SECRET_PAIRS = re.compile(r'''(?i)(['"]?(?:password|password_hash|access_token|refresh_token|id_token|client_secret|jwt_secret|token)['"]?\s*[:=]\s*)(['"])[^'"]*\2''')


def sensitive_field(key: str) -> bool:
    normalized = key.lower()
    return normalized in SENSITIVE_FIELDS or normalized.endswith(('_password', '_secret', '_token'))


def redact_payload(value, secrets: tuple[str, ...]):
    if isinstance(value, dict):
        return {key: '[REDACTED]' if sensitive_field(str(key)) else redact_payload(item, secrets) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_payload(item, secrets) for item in value]
    if isinstance(value, str):
        result = URI_AUTH.sub(r'\1[REDACTED]@', value)
        result = SECRET_PAIRS.sub(lambda match: match[1]+match[2]+'[REDACTED]'+match[2], result)
        for secret in secrets:
            result = result.replace(secret, '[REDACTED]')
        return result
    return value


def environment_secrets() -> tuple[str, ...]:
    return tuple(sorted({value for key, value in os.environ.items() if sensitive_field(key) and len(value) >= 4}, key=len, reverse=True))
