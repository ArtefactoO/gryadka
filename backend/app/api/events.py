"""
Generic Server-Sent Events API.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Optional

from fastapi import APIRouter, Cookie, Header, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from app.auth import verify_token
from app.api.auth import AUTH_COOKIE_NAME, _materialize_current_user
from app.authorization import has_permission
from app.services.sse_manager import sse_manager

router = APIRouter(prefix="/api/events", tags=["events"])


def _extract_token(authorization: Optional[str]) -> str:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()
    return ""


@router.get("/stream")
async def stream_events(
    request: Request,
    target_type: Optional[str] = Query(default=None),
    target_name: Optional[str] = Query(default=None),
    event: Optional[str] = Query(default=None),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    auth_cookie: Optional[str] = Cookie(default=None, alias=AUTH_COOKIE_NAME),
):
    jwt_token = _extract_token(authorization) or (auth_cookie.strip() if auth_cookie else "")
    if not jwt_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing auth token")
    try:
        user = _materialize_current_user(verify_token(jwt_token))
        if not has_permission(user, "targets.read"):
            raise HTTPException(403, "Permission denied")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid auth token") from None

    subscriber_id = str(uuid.uuid4())
    queue = await sse_manager.subscribe(
        subscriber_id=subscriber_id,
        target_type=(target_type.strip() if target_type else None),
        target_name=(target_name.strip() if target_name else None),
        event=(event.strip() if event else None),
    )

    async def _event_generator():
        ready_payload = {
            "event": "sse_ready",
            "target_type": target_type,
            "target_name": target_name,
        }
        yield f"event: sse_ready\ndata: {json.dumps(ready_payload)}\n\n"
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=20)
                    event_name = str(payload.get("event") or "message")
                    yield f"event: {event_name}\ndata: {json.dumps(payload, default=str)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            await sse_manager.unsubscribe(subscriber_id)

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers=headers,
    )
