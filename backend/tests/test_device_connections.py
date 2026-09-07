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


async def test_retire_closes_only_the_matching_current_lease() -> None:
    manager = DeviceConnectionManager()
    first_socket = _WebSocket()
    second_socket = _WebSocket()
    first = await manager.connect("DEVICE-3", first_socket, "session-1")  # type: ignore[arg-type]
    second = await manager.connect("DEVICE-3", second_socket, "session-2")  # type: ignore[arg-type]

    assert await manager.retire(first, code=1011, reason="stale timeout") is False
    assert second_socket.closed is None
    assert await manager.is_current(second)

    assert await manager.retire(second, code=1011, reason="tts ready timeout") is True
    assert second_socket.closed == (1011, "tts ready timeout")
    assert not await manager.is_current(second)


async def test_delayed_ownership_revocation_does_not_close_reclaimed_device() -> None:
    manager = DeviceConnectionManager()
    old_socket = _WebSocket()
    old = await manager.connect("DEVICE-4", old_socket, "old", reset_epoch=0)
    assert await manager.revoke_ownership("DEVICE-4", 1)
    assert old.revoked.is_set()
    assert not await manager.send_bytes_for_lease(old, b"late-audio")
    new_socket = _WebSocket()
    new = await manager.connect("DEVICE-4", new_socket, "new", reset_epoch=1)
    assert not await manager.revoke_ownership("DEVICE-4", 1)
    assert await manager.is_current(new)
    assert not new.revoked.is_set()
