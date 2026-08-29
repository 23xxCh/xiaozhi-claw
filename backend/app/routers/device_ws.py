import asyncio

from fastapi import APIRouter, WebSocket

from backend.realtime.session import serve_device_websocket

router = APIRouter(tags=["device-websocket"])


@router.websocket("/v1/device/ws")
async def device_websocket(websocket: WebSocket) -> None:
    try:
        await serve_device_websocket(websocket)
    except asyncio.CancelledError:
        # ASGI servers cancel a WebSocket handler when the peer disappears
        # mid-turn. The session layer has already canceled audio work and
        # scheduled bounded persistence cleanup, so this is a normal close.
        return
