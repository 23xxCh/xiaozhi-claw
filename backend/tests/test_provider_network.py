import socket

import pytest

from backend.app.provider_network import build_host_override_resolver, parse_host_overrides


def test_provider_host_override_uses_pinned_ip_and_delegates_other_hosts() -> None:
    calls: list[str] = []

    def resolver(host: str, *args: object, **kwargs: object) -> list[tuple[str, int]]:
        del args, kwargs
        calls.append(host)
        return [(host, 443)]

    overridden = build_host_override_resolver(
        "dashscope.aliyuncs.com=39.96.213.166,api.deepseek.com=124.225.27.128",
        resolver=resolver,
    )

    assert overridden("dashscope.aliyuncs.com", 443) == [("39.96.213.166", 443)]
    assert overridden("example.com", 443) == [("example.com", 443)]
    assert calls == ["39.96.213.166", "example.com"]


@pytest.mark.parametrize(
    "raw",
    [
        "missing-separator",
        "dashscope.aliyuncs.com=not-an-ip",
        "=39.96.213.166",
    ],
)
def test_provider_host_override_rejects_invalid_entries(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_host_overrides(raw)


def test_empty_provider_host_override_keeps_system_resolver() -> None:
    resolver = build_host_override_resolver("", resolver=socket.getaddrinfo)
    assert resolver is socket.getaddrinfo
