import asyncio
"""
Authentication API endpoints
"""
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from typing import Optional

from app.config import get_app_config, settings
from app.auth import authenticate_user, create_access_token, verify_token
from fastapi.responses import RedirectResponse
from app.services.oidc_service import get_oidc_client, oidc_configuration, user_from_oidc_claims
from app.audit import audit_log
from app.authorization import build_user_payload, has_permission

router = APIRouter(prefix="/api/auth", tags=["auth"])
optional_security = HTTPBearer(auto_error=False)
AUTH_COOKIE_NAME = "gryadka_auth"


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    display_name: Optional[str] = None
    auth_provider: Optional[str] = None
    roles: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)


class UserResponse(BaseModel):
    username: str
    display_name: Optional[str] = None
    auth_provider: Optional[str] = None
    roles: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)


def _normalize_token_payload(payload: dict) -> dict:
    # Backward-compatible normalization for older tokens:
    # historically some payloads could carry username without "sub".
    if isinstance(payload, dict) and not payload.get("sub") and payload.get("username"):
        payload["sub"] = payload["username"]
    return payload


def _materialize_current_user(payload: dict) -> dict:
    normalized = _normalize_token_payload(payload)
    if normalized.get('auth_provider') == 'local':
        config = get_app_config()
        username = normalized.get('sub') or normalized.get('username')
        account = next((u for u in config.users if u.get('username') == username), None) if config else None
        if account is None:
            raise HTTPException(401, 'Локальная учетная запись недоступна.')
        return build_user_payload(username=username, auth_provider='local',
                                  display_name=account.get('display_name'), roles=account.get('roles') or ['viewer'])
    if normalized.get("auth_provider") == "authentik":
        return build_user_payload(username=normalized["sub"], display_name=normalized.get("display_name"), email=normalized.get("email"),
                                  auth_provider="authentik", groups=normalized.get("groups", []))
    if str(normalized.get("auth_provider") or "").strip().lower() != "freeipa":
        return normalized

    # Prefer compact JWT payloads that already carry resolved roles/permissions.
    if normalized.get("roles") or normalized.get("permissions"):
        return normalized

    username = str(normalized.get("sub") or normalized.get("username") or "").strip()
    if not username:
        return normalized

    return build_user_payload(
        username=username,
        display_name=normalized.get("display_name"),
        email=normalized.get("email"),
        groups=normalized.get("groups", []),
        auth_provider="freeipa",
    )


def _extract_token(
    credentials: Optional[HTTPAuthorizationCredentials],
    cookie_token: Optional[str],
) -> Optional[str]:
    if credentials and credentials.credentials:
        return credentials.credentials
    if cookie_token:
        return cookie_token.strip()
    return None


def _request_is_secure(request: Request) -> bool:
    forwarded_proto = str(request.headers.get("x-forwarded-proto") or "").strip().lower()
    if forwarded_proto:
        return forwarded_proto == "https"
    return request.url.scheme == "https"


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(optional_security),
    cookie_token: Optional[str] = Cookie(default=None, alias=AUTH_COOKIE_NAME),
) -> dict:
    """Dependency to get current authenticated user."""
    token = _extract_token(credentials, cookie_token)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Требуется вход в систему.",
        )
    payload = verify_token(token)
    return _materialize_current_user(payload)


def get_optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(optional_security),
    cookie_token: Optional[str] = Cookie(default=None, alias=AUTH_COOKIE_NAME),
) -> Optional[dict]:
    """Optional authentication dependency for RO endpoints."""
    token = _extract_token(credentials, cookie_token)
    if not token:
        return None
    try:
        payload = verify_token(token)
    except HTTPException:
        return None
    return _materialize_current_user(payload)


def require_permission(permission: str):
    def dependency(request: Request, current_user: dict = Depends(get_current_user)) -> dict:
        scheme, _, token = request.headers.get('authorization', '').partition(' ')
        bearer_authenticated = scheme.lower() == 'bearer' and bool(token.strip())
        if request.method not in {"GET", "HEAD", "OPTIONS"} and not bearer_authenticated:
            if request.headers.get("origin") != settings.public_base_url.rstrip("/"):
                raise HTTPException(403, "Этот источник запроса не разрешен для изменения данных.")
        if not has_permission(current_user, permission):
            audit_log(
                "permission_denied",
                user=current_user,
                request=request,
                permission=permission,
                status="denied",
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Недостаточно прав: {permission}",
            )
        return current_user

    return dependency


@router.post("/login", response_model=LoginResponse)
async def login(login_data: LoginRequest, request: Request, response: Response):
    """Authenticate user and return JWT token"""
    if not oidc_configuration()["allow_local"]:
        raise HTTPException(403, "Локальный вход отключен.")
    user = await asyncio.to_thread(authenticate_user, login_data.username, login_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный логин или пароль."
        )

    access_token = create_access_token(data=user)
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=access_token,
        httponly=True,
        secure=_request_is_secure(request),
        samesite="lax",
        max_age=settings.jwt_expiration_hours * 3600,
        path="/",
    )
    return LoginResponse(
        access_token=access_token,
        username=user["username"],
        display_name=user.get("display_name"),
        auth_provider=user.get("auth_provider"),
        roles=user.get("roles", []),
        permissions=user.get("permissions", []),
    )


@router.post("/logout")
async def logout(response: Response):
    """Logout endpoint (client should remove token)"""
    response.delete_cookie(key=AUTH_COOKIE_NAME, path="/")
    return {"message": "Logged out successfully"}


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(current_user: dict = Depends(get_current_user)):
    """Get current authenticated user information"""
    return UserResponse(
        username=current_user.get("sub") or current_user.get("username") or "",
        display_name=current_user.get("display_name"),
        auth_provider=current_user.get("auth_provider"),
        roles=current_user.get("roles", []),
        permissions=current_user.get("permissions", []),
    )


@router.get("/config")
async def get_auth_configuration():
    config = oidc_configuration()
    return {"sso_enabled": config["enabled"], "local_enabled": config["allow_local"]}


@router.get("/oidc/login")
async def oidc_login(request: Request):
    return await get_oidc_client().authorize_redirect(request, settings.public_base_url.rstrip("/") + "/api/auth/oidc/callback")


@router.get("/oidc/callback")
async def oidc_callback(request: Request):
    try:
        token = await get_oidc_client().authorize_access_token(request)
        if not token.get("id_token") or not token.get("userinfo"):
            raise HTTPException(401, "Сервис входа не вернул ID token.")
        user = user_from_oidc_claims(dict(token["userinfo"]))
    except HTTPException:
        request.session.clear()
        raise
    except Exception:
        request.session.clear()
        raise HTTPException(401, "Не удалось завершить вход через Authentik. Повторите вход.") from None
    request.session.clear()
    response = RedirectResponse(settings.public_base_url.rstrip("/") + "/", status_code=303)
    response.set_cookie(AUTH_COOKIE_NAME, create_access_token(user), httponly=True,
                        secure=settings.public_base_url.startswith("https://"), samesite="lax",
                        max_age=settings.jwt_expiration_hours * 3600, path="/")
    audit_log("oidc_login", user=user, request=request)
    return response
