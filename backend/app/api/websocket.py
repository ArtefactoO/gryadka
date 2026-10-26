"""
WebSocket endpoint for push notifications
"""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from app.api.auth import AUTH_COOKIE_NAME, _materialize_current_user
from app.authorization import has_permission
from app.config import settings
from app.auth import verify_token
from app.services.websocket_manager import websocket_manager

router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time updates"""
    if websocket.headers.get("origin") != settings.public_base_url.rstrip("/"):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Untrusted Origin")
        return
    auth_cookie = websocket.cookies.get(AUTH_COOKIE_NAME)
    if not auth_cookie:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Missing auth token")
        return
    try:
        user = _materialize_current_user(verify_token(auth_cookie))
        if not has_permission(user, "targets.read"):
            raise ValueError("Permission denied")
    except Exception:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid auth token")
        return

    await websocket_manager.connect(websocket)
    try:
        while True:
            # Keep connection alive and wait for messages
            data = await websocket.receive_text()
            # Echo back or handle client messages if needed
            await websocket.send_json({"type": "pong", "message": data})
    except WebSocketDisconnect:
        websocket_manager.disconnect(websocket)
