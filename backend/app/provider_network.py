import ipaddress
import socket
from collections.abc import Callable
from typing import Any

GetAddrInfo = Callable[..., list[tuple[Any, ...]]]
_SYSTEM_GETADDRINFO = socket.getaddrinfo


def parse_host_overrides(raw: str) -> dict[str, str]:
    overrides: dict[str, str] = {}
    for item in (part.strip() for part in raw.split(",")):
        if not item:
            continue
        if "=" not in item:
            raise ValueError("provider host override must use host=ip")
        host, address = (part.strip() for part in item.split("=", 1))
        if not host or any(character.isspace() for character in host):
            raise ValueError("provider host override contains an invalid host")
        try:
            ipaddress.ip_address(address)
        except ValueError as exc:
            raise ValueError("provider host override contains an invalid IP address") from exc
        overrides[host.lower()] = address
    return overrides


def build_host_override_resolver(
    raw: str,
    *,
    resolver: GetAddrInfo = _SYSTEM_GETADDRINFO,
) -> GetAddrInfo:
    overrides = parse_host_overrides(raw)
    if not overrides:
        return resolver

    def resolve(host: str | bytes, *args: Any, **kwargs: Any) -> list[tuple[Any, ...]]:
        lookup = host.decode("ascii") if isinstance(host, bytes) else host
        target = overrides.get(lookup.lower(), lookup)
        return resolver(target, *args, **kwargs)

    return resolve


def install_provider_host_overrides(raw: str) -> None:
    if not raw.strip():
        return
    socket.getaddrinfo = build_host_override_resolver(raw)
