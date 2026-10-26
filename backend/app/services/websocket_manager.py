"""
WebSocket connection manager for push notifications
"""
from typing import List
from fastapi import WebSocket
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class WebSocketManager:
    """Manages WebSocket connections and sends push notifications"""

    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        """Accept new WebSocket connection"""
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.debug(f"WebSocket connected. Total connections: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        """Remove WebSocket connection"""
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        logger.debug(f"WebSocket disconnected. Total connections: {len(self.active_connections)}")

    async def send_personal_message(self, message: dict, websocket: WebSocket):
        """Send message to specific WebSocket connection"""
        try:
            await websocket.send_json(message)
        except Exception as e:
            logger.error(f"Error sending message to WebSocket: {e}")
            self.disconnect(websocket)

    async def broadcast(self, message: dict):
        """Broadcast message to all connected clients"""
        if not self.active_connections:
            return

        disconnected = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.error(f"Error broadcasting to WebSocket: {e}")
                disconnected.append(connection)

        # Remove disconnected connections
        for connection in disconnected:
            self.disconnect(connection)

    async def notify_data_updated(self, clusters: List[str]):
        """Notify all clients that data has been updated"""
        target_ids = [item for item in clusters if ":" in item]
        message = {
            "event": "data_updated",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "clusters": clusters
        }
        if target_ids:
            message["target_ids"] = target_ids
            message["targets"] = [
                {
                    "target_type": target_id.split(":", 1)[0],
                    "target_name": target_id.split(":", 1)[1],
                    "target_id": target_id,
                }
                for target_id in target_ids
            ]
        await self.broadcast(message)
        logger.debug(f"Notified {len(self.active_connections)} clients about update: {clusters}")


# Global WebSocket manager instance
websocket_manager = WebSocketManager()
