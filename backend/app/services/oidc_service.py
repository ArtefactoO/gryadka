"""Authentik OIDC authorization-code flow with PKCE and validated ID tokens."""
from functools import lru_cache

from authlib.integrations.starlette_client import OAuth
from fastapi import HTTPException

from app.authorization import build_user_payload
from app.config import get_app_config, settings


def oidc_configuration() -> dict:
    config = get_app_config()
    auth = config.auth_config if config else {}
    oidc = auth.get('oidc') or {}
    enabled = auth.get('provider') == 'authentik' and bool(oidc.get('issuer'))
    return {'enabled': enabled, 'issuer': oidc.get('issuer'),
            'allow_local': auth.get('provider', 'local') == 'local' or bool(auth.get('allow_local_fallback', False))}


@lru_cache(maxsize=1)
def get_oidc_client():
    config = oidc_configuration()
    issuer = str(config.get('issuer') or '').rstrip('/')
    if not config['enabled'] or not settings.oidc_client_id or not settings.oidc_client_secret:
        raise HTTPException(503, 'Authentik SSO is not configured')
    if not issuer.startswith('https://') and not settings.oidc_allow_insecure_local:
        raise HTTPException(503, 'OIDC issuer must use HTTPS')
    oauth = OAuth()
    oauth.register('authentik', client_id=settings.oidc_client_id, client_secret=settings.oidc_client_secret,
                   server_metadata_url=f'{issuer}/.well-known/openid-configuration',
                   client_kwargs={'scope': 'openid profile email groups', 'code_challenge_method': 'S256', 'timeout': 10})
    return oauth.create_client('authentik')


def user_from_oidc_claims(claims: dict) -> dict:
    subject = claims.get('sub')
    groups = claims.get('groups', [])
    if not isinstance(subject, str) or not subject or not isinstance(groups, list) or not all(isinstance(g, str) for g in groups):
        raise HTTPException(401, 'Invalid OIDC identity claims')
    user = build_user_payload(username=f'oidc:{subject}', display_name=claims.get('name') or claims.get('preferred_username') or subject,
                              email=claims.get('email'), groups=groups, auth_provider='authentik')
    if not user['permissions']:
        raise HTTPException(403, 'No Gryadka role is assigned to this Authentik account')
    return user
