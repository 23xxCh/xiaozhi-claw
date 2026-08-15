import logging

from fastapi import WebSocket

logger = logging.getLogger(__name__)


async def send_error(websocket: WebSocket, serial: str, code: str, message: str) -> None:
    delivered = await websocket.app.state.device_connections.send_json(
        serial, {"type": "error", "code": code, "message": message}
    )
    if not delivered:
        logger.info("device %s disconnected before error %s was delivered", serial, code)
