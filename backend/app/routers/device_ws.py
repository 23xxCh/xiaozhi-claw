from fastapi import APIRouter, WebSocket

from backend.realtime.session import serve_device_websocket

router = APIRouter(tags=["device-websocket"])


@router.websocket("/v1/device/ws")
async def device_websocket(websocket: WebSocket) -> None:
    await serve_device_websocket(websocket)
