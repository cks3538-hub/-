"""security.network 시험: origin 검사, OutboundGuard(offline 차단·허용 loopback 통과), make_client 정책, redirect/Retry-After.

OutboundGuard 는 프로세스 내 socket API 검사이며 OS 수준 격리가 아니다. 여기서는 그 계약만 검증한다 (MOCK 수준).
"""

from __future__ import annotations

import io
import logging
import socket
from pathlib import Path

import pytest
from test_adapters_mockserver import MockServer

from corp_dl_agent.config import load_config
from corp_dl_agent.errors import AgentError
from corp_dl_agent.logs import MaskingFormatter
from corp_dl_agent.security.network import (
    OutboundGuard,
    approved_origins_from_config,
    assert_origin_allowed,
    is_loopback_host,
    is_origin_allowed,
    make_client,
    parse_origin,
    parse_retry_after,
    reject_redirect,
)
from corp_dl_agent.security.secrets import REGISTRY


def offline_cfg(tmp_path: Path):  # type: ignore[no-untyped-def]
    return load_config(overrides={"paths.data_root": str(tmp_path)})


def gateway_cfg(tmp_path: Path, port: int):  # type: ignore[no-untyped-def]
    return load_config(
        overrides={
            "profile": "corp-gateway",
            "gateway.enabled": "true",
            "gateway.base_url": f"http://127.0.0.1:{port}",
            "gateway.model_id": "synthetic-model-id",
            "gateway.secret_ref": "env:CORP_DL_AGENT_TEST_TOKEN",
            "gateway.approved_origins": f"[http://127.0.0.1:{port}]",
            "paths.data_root": str(tmp_path),
        }
    )


# --------------------------------------------------------------------------- origin


def test_parse_origin_and_defaults() -> None:
    o = parse_origin("https://gw.example.internal/v1/chat")
    assert (o.scheme, o.host, o.port) == ("https", "gw.example.internal", 443)
    assert str(parse_origin("http://127.0.0.1:8080/x")) == "http://127.0.0.1:8080"
    assert parse_origin("HTTPS://Host.Example:8443").host == "host.example"


@pytest.mark.parametrize(
    "bad", ["", "ftp://x", "https://", "https://user:pw@host/x", "not a url", "file:///etc/passwd"]
)
def test_parse_origin_rejects(bad: str) -> None:
    with pytest.raises(AgentError) as ei:
        parse_origin(bad)
    assert ei.value.code == "E_ORIGIN_NOT_ALLOWED"


def test_is_origin_allowed_exact_host_port_scheme() -> None:
    allowed = ["https://gw.example.internal", "http://127.0.0.1:9000"]
    assert is_origin_allowed("https://gw.example.internal/v1/messages", allowed)
    assert is_origin_allowed("https://gw.example.internal:443/", allowed)
    assert not is_origin_allowed("http://gw.example.internal/v1", allowed)  # scheme 다름
    assert not is_origin_allowed("https://gw.example.internal:8443/v1", allowed)  # port 다름
    assert not is_origin_allowed("https://evil.example/gw.example.internal", allowed)
    assert not is_origin_allowed("http://127.0.0.1:9001/", allowed)
    assert not is_origin_allowed("garbage", allowed)
    assert not is_origin_allowed("https://gw.example.internal", [])  # 빈 목록 -> 아무것도 허용 안 함


def test_assert_origin_allowed_details_have_no_secret() -> None:
    with pytest.raises(AgentError) as ei:
        assert_origin_allowed("https://public.example/v1?key=sk-abcdefghijklmnop", ["https://gw.internal"])
    err = ei.value
    assert err.code == "E_ORIGIN_NOT_ALLOWED"
    assert "sk-abcdefghijklmnop" not in str(err) and "sk-abcdefghijklmnop" not in str(err.details)


def test_is_loopback_host() -> None:
    assert is_loopback_host("127.0.0.1") and is_loopback_host("localhost") and is_loopback_host("::1")
    assert (
        is_loopback_host("127.5.6.7")
        and not is_loopback_host("10.0.0.1")
        and not is_loopback_host("example.com")
    )


def test_approved_origins_from_config_empty_when_offline(tmp_path: Path) -> None:
    assert approved_origins_from_config(offline_cfg(tmp_path)) == []
    cfg = gateway_cfg(tmp_path, 9999)
    assert approved_origins_from_config(cfg) == ["http://127.0.0.1:9999"]


# --------------------------------------------------------------------------- OutboundGuard


def test_guard_blocks_external_connect_in_offline(tmp_path: Path) -> None:
    cfg = offline_cfg(tmp_path)
    guard = OutboundGuard.from_config(cfg)
    assert guard.active and guard.describe()["os_level_isolation"] is False
    original_cc = socket.create_connection
    with guard:
        assert guard.installed
        with pytest.raises(AgentError) as ei:
            socket.create_connection(("203.0.113.1", 443), timeout=0.5)  # TEST-NET, 실제 연결 없이 차단
        assert ei.value.code == "E_NETWORK_BLOCKED"
        with pytest.raises(AgentError) as ei2:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                s.connect(("203.0.113.2", 80))
            finally:
                s.close()
        assert ei2.value.code == "E_NETWORK_BLOCKED"
        with pytest.raises(AgentError) as ei3:
            socket.getaddrinfo("gateway.example.invalid", 443)  # DNS 조회 자체를 차단
        assert ei3.value.code == "E_NETWORK_BLOCKED"
        s2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            with pytest.raises(AgentError):
                s2.connect_ex(("203.0.113.3", 443))
        finally:
            s2.close()
    assert not guard.installed
    assert socket.create_connection is original_cc
    assert len(guard.blocked_attempts) == 4
    assert {b.api for b in guard.blocked_attempts} == {
        "socket.create_connection",
        "socket.connect",
        "socket.getaddrinfo",
        "socket.connect_ex",
    }


def test_guard_allows_only_listed_loopback(tmp_path: Path) -> None:
    import httpx

    cfg = offline_cfg(tmp_path)
    with MockServer() as srv, MockServer() as other:
        srv.enqueue(200, {"ok": True})
        with OutboundGuard.from_config(cfg, extra_allowed=[srv.base_url]) as guard:
            # 허용 목록의 loopback:port 만 통과
            with httpx.Client(follow_redirects=False, trust_env=False, timeout=5.0) as c:
                r = c.get(srv.base_url + "/ok")
            assert r.status_code == 200 and len(srv.requests) == 1
            # 같은 loopback 이라도 목록에 없는 port 는 차단 (loopback 이 자동 허용되지 않는다)
            with pytest.raises(AgentError) as ei:
                socket.create_connection(("127.0.0.1", other.port), timeout=1)
            assert ei.value.code == "E_NETWORK_BLOCKED" and other.requests == []
            with pytest.raises((AgentError, httpx.HTTPError)):
                with httpx.Client(follow_redirects=False, trust_env=False, timeout=5.0) as c:
                    c.get(other.base_url + "/blocked")
            assert other.requests == []
        assert guard.blocked_attempts and all(b.port == other.port for b in guard.blocked_attempts)


def test_guard_without_extra_allowed_blocks_loopback_too(tmp_path: Path) -> None:
    with MockServer() as srv, OutboundGuard.from_config(offline_cfg(tmp_path)):
        with pytest.raises(AgentError) as ei:
            socket.create_connection(("127.0.0.1", srv.port), timeout=1)
        assert ei.value.code == "E_NETWORK_BLOCKED"
        assert srv.requests == []


def test_guard_inactive_for_gateway_profile(tmp_path: Path) -> None:
    guard = OutboundGuard.from_config(gateway_cfg(tmp_path, 9000))
    assert not guard.active
    original = socket.create_connection
    with guard:
        assert socket.create_connection is original  # 가로채지 않음
        assert not guard.installed


def test_guard_reentrant_install(tmp_path: Path) -> None:
    guard = OutboundGuard.from_config(offline_cfg(tmp_path))
    original = socket.create_connection
    with guard, guard:
        assert guard.installed and socket.create_connection is not original
    assert socket.create_connection is original


def test_guard_af_unix_not_intercepted(tmp_path: Path) -> None:
    if not hasattr(socket, "AF_UNIX"):
        pytest.skip("AF_UNIX 없음 (Windows)")
    with OutboundGuard.from_config(offline_cfg(tmp_path)):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            with pytest.raises(OSError):  # guard 가 아닌 OS 오류 (없는 경로)
                s.connect(str(tmp_path / "no-such.sock"))
        finally:
            s.close()


# --------------------------------------------------------------------------- make_client / redirect / retry-after


def test_make_client_refuses_offline(tmp_path: Path) -> None:
    with pytest.raises(AgentError) as ei:
        make_client(offline_cfg(tmp_path))
    assert ei.value.code == "E_NETWORK_BLOCKED"


def test_make_client_policy_in_gateway_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import certifi

    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:3128")  # trust_env=False 이므로 무시되어야 함
    cfg = gateway_cfg(tmp_path, 9000)
    client = make_client(cfg, None, timeout=7.5)
    try:
        assert client.follow_redirects is False
        assert client.trust_env is False
        assert client.max_redirects == 0
        assert client.timeout.connect == 7.5
        assert client._mounts == {} or all("proxy" not in str(k) for k in client._mounts)
    finally:
        client.close()
    with_ca = make_client(cfg, certifi.where())
    with_ca.close()
    with pytest.raises(AgentError) as ei:
        make_client(cfg, str(tmp_path / "missing-ca.pem"))
    assert ei.value.code == "E_BLOCKED_CONFIG"


def test_reject_redirect() -> None:
    reject_redirect(200, None, context="x")
    with pytest.raises(AgentError) as ei:
        reject_redirect(302, "https://public.example/v1/chat/completions", context="gateway")
    assert ei.value.code == "E_ORIGIN_NOT_ALLOWED"
    assert ei.value.details["location_origin"] == "https://public.example:443"
    with pytest.raises(AgentError):
        reject_redirect(307, "/relative", context="gateway")


def test_parse_retry_after() -> None:
    assert parse_retry_after(None) == 1.0
    assert parse_retry_after("3") == 3.0
    assert parse_retry_after("999") == 30.0  # cap
    assert parse_retry_after("-5") == 0.0
    assert parse_retry_after("garbage", default=2.0) == 2.0
    from datetime import UTC, datetime, timedelta
    from email.utils import format_datetime

    future = format_datetime(datetime.now(UTC) + timedelta(seconds=5))
    assert 0.0 <= parse_retry_after(future) <= 5.5


def test_secret_registry_masks_logs_and_text() -> None:
    secret = "sk-networktestsecret0001"
    REGISTRY.register(secret)
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(MaskingFormatter("%(message)s"))
    logger = logging.getLogger("corp_dl_agent.test_network")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        logger.info("Authorization: Bearer %s", secret)
    finally:
        logger.removeHandler(handler)
    assert secret not in buf.getvalue() and "***" in buf.getvalue()
    assert secret not in REGISTRY.mask(f"x-api-key: {secret}")
