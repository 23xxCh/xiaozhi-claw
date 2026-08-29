from backend.app.device_connections import DeviceConnectionManager


class _WebSocket:
    def __init__(self) -> None:
        self.json_messages: list[dict[str, object]] = []
        self.binary_messages: list[bytes] = []
        self.closed: tuple[int, str] | None = None

    async def send_json(self, payload: dict[str, object]) -> None:
        self.json_messages.append(payload)

    async def send_bytes(self, payload: bytes) -> None:
        self.binary_messages.append(payload)

    async def close(self, *, code: int, reason: str) -> None:
        self.closed = (code, reason)


async def test_reconnect_retires_old_lease_without_redirecting_old_turn() -> None:
    manager = DeviceConnectionManager()
    first_socket = _WebSocket()
    second_socket = _WebSocket()
    first = await manager.connect("DEVICE-1", first_socket, "session-1")  # type: ignore[arg-type]

    assert await manager.send_bytes_for_lease(first, b"old-before-reconnect")
    second = await manager.connect("DEVICE-1", second_socket, "session-2")  # type: ignore[arg-type]

    assert first_socket.closed == (1012, "device reconnected")
    assert not await manager.send_bytes_for_lease(first, b"stale-audio")
    assert await manager.send_bytes_for_lease(second, b"new-audio")
    assert first_socket.binary_messages == [b"old-before-reconnect"]
    assert second_socket.binary_messages == [b"new-audio"]


async def test_old_disconnect_cannot_remove_new_lease() -> None:
    manager = DeviceConnectionManager()
    first = await manager.connect("DEVICE-2", _WebSocket(), "session-1")  # type: ignore[arg-type]
    second_socket = _WebSocket()
    second = await manager.connect("DEVICE-2", second_socket, "session-2")  # type: ignore[arg-type]

    await manager.disconnect(first)

    assert await manager.is_current(second)
    assert await manager.send_json("DEVICE-2", {"type": "ping"})
    assert second_socket.json_messages == [
        {"session_id": "session-2", "type": "ping"}
    ]
