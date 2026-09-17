"""outbound 네트워크 정책: origin 허용 검사, 프로세스 내 outbound guard, redirect 금지 HTTP client helper.

핵심 규칙
- 네트워크 허용 여부는 `AppConfig.network_allowed()` 만 신뢰한다 (corp-gateway + gateway.enabled).
- 허용된 origin(`scheme://host[:port]`) 밖으로는 요청하지 않는다. 값이 없으면 public provider 로 fallback 하지 않는다.
- redirect 를 따르지 않고(3xx 는 오류), TLS 검증을 끄지 않으며, 개인 환경의 프록시/환경 변수(HTTP(S)_PROXY, .netrc 등) 를 상속하지 않는다.

OutboundGuard 의 한계 (BUILD_SPEC [16] G):
이 guard 는 **프로세스 내 Python socket API 검사**이며 OS/방화벽 수준의 네트워크 격리가 아니다.
`socket.create_connection`, `socket.socket.connect/connect_ex`, `socket.getaddrinfo` 를 가로채어
허용 목록 밖의 연결 시도를 E_NETWORK_BLOCKED 로 막고 기록한다. C 확장/서브프로세스가 직접 여는 소켓,
raw socket, 이미 열린 연결은 검사하지 못한다. "단순 socket mock 시험" 과 "실제 network isolation 환경에서의
설치/실행 시험(TARGET_OFFLINE_TESTED)" 은 구분해서 보고해야 한다.
"""

from __future__ import annotations

import ipaddress
import socket
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from corp_dl_agent.common import now_iso
from corp_dl_agent.config.schemas import AppConfig
from corp_dl_agent.errors import AgentError

DEFAULT_PORTS = {"http": 80, "https": 443}
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


@dataclass(frozen=True)
class Origin:
    scheme: str
    host: str
    port: int

    def __str__(self) -> str:
        return f"{self.scheme}://{self.host}:{self.port}"

    @property
    def is_loopback(self) -> bool:
        return is_loopback_host(self.host)


def is_loopback_host(host: str) -> bool:
    h = host.strip().lower().strip("[]")
    if h in LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def parse_origin(url: str) -> Origin:
    """URL 에서 scheme/host/port 만 추출한다. http(s) 외 scheme, host 없음, userinfo 포함은 거부."""
    if not isinstance(url, str) or not url.strip():
        raise AgentError("E_ORIGIN_NOT_ALLOWED", "빈 URL 은 허용되지 않습니다")
    parts = urlsplit(url.strip())
    scheme = (parts.scheme or "").lower()
    if scheme not in DEFAULT_PORTS:
        raise AgentError(
            "E_ORIGIN_NOT_ALLOWED",
            f"지원되지 않는 URL scheme 입니다: '{scheme or '(없음)'}' (http/https 만 허용)",
        )
    if parts.username is not None or parts.password is not None:
        raise AgentError("E_ORIGIN_NOT_ALLOWED", "URL 에 사용자 정보(userinfo) 를 포함할 수 없습니다")
    host = (parts.hostname or "").lower()
    if not host:
        raise AgentError("E_ORIGIN_NOT_ALLOWED", "URL 에 host 가 없습니다")
    try:
        port = parts.port
    except ValueError as exc:
        raise AgentError("E_ORIGIN_NOT_ALLOWED", "URL 의 port 가 올바르지 않습니다") from exc
    return Origin(scheme=scheme, host=host, port=port or DEFAULT_PORTS[scheme])


def parse_origins(urls: Iterable[str]) -> list[Origin]:
    return [parse_origin(u) for u in urls]


def is_origin_allowed(url: str, allowed: Iterable[str]) -> bool:
    """url 의 origin(scheme+host+port) 이 allowed 목록의 origin 과 정확히 일치하면 True. 파싱 실패는 False."""
    try:
        target = parse_origin(url)
        allowed_set = {parse_origin(a) for a in allowed}
    except AgentError:
        return False
    return target in allowed_set


def assert_origin_allowed(url: str, allowed: Iterable[str], *, context: str = "") -> Origin:
    """허용되지 않으면 E_ORIGIN_NOT_ALLOWED. 비밀값이 없는 origin 정보만 details 에 남긴다."""
    origin = parse_origin(url)
    allowed_list = list(allowed)
    if not is_origin_allowed(url, allowed_list):
        raise AgentError(
            "E_ORIGIN_NOT_ALLOWED",
            f"허용되지 않은 origin 입니다: {origin}" + (f" ({context})" if context else ""),
            details={
                "origin": str(origin),
                "approved_origins": [str(o) for o in parse_origins(allowed_list)],
            },
        )
    return origin


def approved_origins_from_config(cfg: AppConfig) -> list[str]:
    """설정에 명시된 모든 approved origin (gateway + integrations). 네트워크가 허용된 프로파일에서만 의미가 있다."""
    if not cfg.network_allowed():
        return []
    origins: list[str] = []
    origins.extend(cfg.gateway.approved_origins)
    origins.extend(cfg.integrations.approved_origins)
    # 중복 제거 (순서 유지)
    seen: set[str] = set()
    out: list[str] = []
    for o in origins:
        if o not in seen:
            seen.add(o)
            out.append(o)
    return out


# --------------------------------------------------------------------------- OutboundGuard


@dataclass
class BlockedAttempt:
    host: str
    port: int | None
    api: str
    at: str = field(default_factory=now_iso)


_INSTALL_LOCK = threading.RLock()
_TLS = threading.local()


class OutboundGuard:
    """프로세스 내 outbound 연결 guard (offline 프로파일에서 활성).

    - active=True 면 컨텍스트 안에서 `socket.create_connection` / `socket.socket.connect` / `connect_ex` /
      `socket.getaddrinfo` 를 가로채어 allowed origins 의 (host, port) 가 아닌 연결을 E_NETWORK_BLOCKED 로 막는다.
    - loopback(127.0.0.1/localhost/::1) 도 기본으로 막는다. 시험용 mock 서버는 `extra_allowed` 에
      "http://127.0.0.1:<port>" 처럼 명시할 때만 통과한다.
    - AF_UNIX 등 INET 계열이 아닌 소켓(로컬 IPC) 은 outbound 가 아니므로 검사하지 않는다.
    - active=False 면 아무것도 가로채지 않는다 (corp-gateway 프로파일: origin 검사는 make_client/adapter 가 수행).

    한계: 이 guard 는 Python 프로세스 안의 socket API 호출만 검사한다. OS 수준 격리(방화벽/네트워크 네임스페이스)가
    아니며, C 확장·자식 프로세스·이미 열린 연결은 막지 못한다. 실제 격리 환경 시험과 혼동하지 말 것.
    """

    def __init__(
        self,
        allowed_origins: Iterable[str] = (),
        *,
        active: bool = True,
        extra_allowed: Iterable[str] = (),
        block_dns: bool = True,
    ) -> None:
        self.active = active
        self.block_dns = block_dns
        self.allowed: list[Origin] = parse_origins(list(allowed_origins) + list(extra_allowed))
        self._allowed_pairs: set[tuple[str, int]] = {(o.host, o.port) for o in self.allowed}
        self._allowed_hosts: set[str] = {o.host for o in self.allowed}
        self.blocked_attempts: list[BlockedAttempt] = []
        self._originals: dict[str, Any] = {}
        self._depth = 0

    @classmethod
    def from_config(cls, cfg: AppConfig, *, extra_allowed: Iterable[str] = ()) -> OutboundGuard:
        """offline 계열 프로파일이면 활성(허용 목록은 extra_allowed 만), corp-gateway+enabled 면 비활성."""
        if cfg.network_allowed():
            return cls(approved_origins_from_config(cfg), active=False, extra_allowed=extra_allowed)
        return cls((), active=True, extra_allowed=extra_allowed)

    # ------------------------------------------------------------------ policy
    def describe(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "kind": "in_process_socket_guard",
            "os_level_isolation": False,
            "allowed_origins": [str(o) for o in self.allowed],
            "blocked_attempts": len(self.blocked_attempts),
            "note": "프로세스 내 socket API 검사이며 OS 수준 네트워크 격리가 아니다",
        }

    def is_address_allowed(self, host: str, port: int | None) -> bool:
        h = str(host).strip().lower().strip("[]")
        if port is None:
            return h in self._allowed_hosts
        return (h, int(port)) in self._allowed_pairs

    def _block(self, host: str, port: int | None, api: str) -> AgentError:
        self.blocked_attempts.append(BlockedAttempt(host=str(host), port=port, api=api))
        target = f"{host}:{port}" if port is not None else str(host)
        return AgentError(
            "E_NETWORK_BLOCKED",
            f"offline 프로파일에서 외부 연결 시도가 차단되었습니다: {target} ({api})",
            details={
                "host": str(host),
                "port": port,
                "api": api,
                "allowed_origins": [str(o) for o in self.allowed],
                "guard": "in_process_socket_guard",
            },
        )

    # ------------------------------------------------------------------ interception
    @staticmethod
    def _addr_parts(address: Any) -> tuple[str, int | None] | None:
        """INET/INET6 주소 튜플 -> (host, port). 그 외(AF_UNIX 경로 등) 는 None."""
        if isinstance(address, tuple) and len(address) >= 2 and isinstance(address[0], str):
            port = address[1]
            return address[0], int(port) if isinstance(port, int) else None
        return None

    def _wrap_create_connection(self, original: Callable[..., Any]) -> Callable[..., Any]:
        guard = self

        def create_connection(address: Any, *args: Any, **kwargs: Any) -> Any:
            parts = guard._addr_parts(address)
            if parts is None:
                raise guard._block(str(address), None, "socket.create_connection")
            host, port = parts
            if not guard.is_address_allowed(host, port):
                raise guard._block(host, port, "socket.create_connection")
            _TLS.allowed_depth = getattr(_TLS, "allowed_depth", 0) + 1
            try:
                return original(address, *args, **kwargs)
            finally:
                _TLS.allowed_depth -= 1

        return create_connection

    def _wrap_connect(self, original: Callable[..., Any], api: str) -> Callable[..., Any]:
        guard = self

        def connect(sock: Any, address: Any, *args: Any, **kwargs: Any) -> Any:
            family = getattr(sock, "family", None)
            if family not in (socket.AF_INET, socket.AF_INET6):
                return original(sock, address, *args, **kwargs)
            if getattr(_TLS, "allowed_depth", 0) > 0:
                return original(sock, address, *args, **kwargs)
            parts = guard._addr_parts(address)
            if parts is None:
                raise guard._block(str(address), None, api)
            host, port = parts
            if not guard.is_address_allowed(host, port):
                raise guard._block(host, port, api)
            return original(sock, address, *args, **kwargs)

        return connect

    def _wrap_getaddrinfo(self, original: Callable[..., Any]) -> Callable[..., Any]:
        guard = self

        def getaddrinfo(host: Any, port: Any, *args: Any, **kwargs: Any) -> Any:
            if host is None or isinstance(host, bytes):
                return original(host, port, *args, **kwargs)
            h = str(host).strip().lower().strip("[]")
            numeric = False
            try:
                ipaddress.ip_address(h)
                numeric = True
            except ValueError:
                pass
            if numeric or h in guard._allowed_hosts or h in LOOPBACK_HOSTS:
                # 숫자 IP / 허용 host / loopback 이름은 DNS 조회 없이(또는 로컬 hosts 로) 해결되므로 통과.
                # 실제 연결 허용 여부는 connect 단계에서 다시 검사한다.
                return original(host, port, *args, **kwargs)
            raise guard._block(h, int(port) if isinstance(port, int) else None, "socket.getaddrinfo")

        return getaddrinfo

    def install(self) -> None:
        """socket API 를 가로챈다 (재진입 가능, 프로세스 전역)."""
        if not self.active:
            return
        with _INSTALL_LOCK:
            if self._depth == 0:
                self._originals = {
                    "create_connection": socket.create_connection,
                    "connect": socket.socket.connect,
                    "connect_ex": socket.socket.connect_ex,
                    "getaddrinfo": socket.getaddrinfo,
                }
                socket.create_connection = self._wrap_create_connection(self._originals["create_connection"])
                socket.socket.connect = self._wrap_connect(self._originals["connect"], "socket.connect")  # type: ignore[method-assign]
                connect_ex = self._wrap_connect(self._originals["connect_ex"], "socket.connect_ex")
                socket.socket.connect_ex = connect_ex  # type: ignore[method-assign]
                if self.block_dns:
                    socket.getaddrinfo = self._wrap_getaddrinfo(self._originals["getaddrinfo"])
            self._depth += 1

    def uninstall(self) -> None:
        if not self.active:
            return
        with _INSTALL_LOCK:
            if self._depth == 0:
                return
            self._depth -= 1
            if self._depth == 0 and self._originals:
                socket.create_connection = self._originals["create_connection"]
                socket.socket.connect = self._originals["connect"]  # type: ignore[method-assign]
                socket.socket.connect_ex = self._originals["connect_ex"]  # type: ignore[method-assign]
                socket.getaddrinfo = self._originals["getaddrinfo"]
                self._originals = {}

    @property
    def installed(self) -> bool:
        return self._depth > 0

    def __enter__(self) -> OutboundGuard:
        self.install()
        return self

    def __exit__(self, *exc: object) -> None:
        self.uninstall()


# --------------------------------------------------------------------------- httpx helpers


def make_client(
    cfg: AppConfig,
    ca_bundle: str | None = None,
    *,
    timeout: float | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    """redirect 금지·TLS 검증 유지·환경 프록시 비상속 httpx.Client.

    - cfg.network_allowed() 가 아니면 E_NETWORK_BLOCKED (client 를 만들지 않는다).
    - verify = ca_bundle(사내 CA 경로) 또는 True. False 는 허용하지 않는다.
    - follow_redirects=False, trust_env=False (HTTP(S)_PROXY/NO_PROXY/.netrc/SSL_CERT_FILE 등 개인 환경 비상속).
    - origin 검사는 호출자가 `assert_origin_allowed` 로 요청 URL 마다 수행한다.
    """
    if not cfg.network_allowed():
        raise AgentError(
            "E_NETWORK_BLOCKED",
            f"프로파일 '{cfg.profile.value}' 에서는 HTTP client 를 만들지 않습니다 (gateway.enabled 및 corp-gateway 필요)",
        )
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - httpx 는 core 의존성
        from corp_dl_agent.errors import blocked_dependency

        raise blocked_dependency("httpx", "network.make_client") from exc
    verify: str | bool = True
    if ca_bundle:
        from pathlib import Path

        p = Path(ca_bundle).expanduser()
        if not p.is_file():
            raise AgentError(
                "E_BLOCKED_CONFIG",
                "ca_bundle 경로에 파일이 없습니다 (TLS 검증을 끄지 않습니다)",
                details={"ca_bundle": p.name},
            )
        verify = str(p)
    t = float(timeout if timeout is not None else cfg.gateway.timeout_seconds)
    return httpx.Client(
        follow_redirects=False,
        verify=verify,
        timeout=httpx.Timeout(t),
        trust_env=False,
        headers=headers or {},
        max_redirects=0,
    )


def reject_redirect(status_code: int, location: str | None, *, context: str) -> None:
    """3xx 응답은 따르지 않고 E_ORIGIN_NOT_ALLOWED 로 보고한다. Location 의 origin 만 details 에 남긴다."""
    if 300 <= int(status_code) < 400:
        loc_origin: str | None = None
        if location:
            try:
                loc_origin = str(parse_origin(location))
            except AgentError:
                loc_origin = "(상대 경로 또는 파싱 불가)"
        raise AgentError(
            "E_ORIGIN_NOT_ALLOWED",
            f"{context}: 서버가 redirect({status_code}) 를 요구했으나 redirect 는 따르지 않습니다",
            details={"status": int(status_code), "location_origin": loc_origin},
        )


def parse_retry_after(value: str | None, *, default: float = 1.0, cap: float = 30.0) -> float:
    """Retry-After 헤더(초 또는 HTTP-date) -> 대기 초. 파싱 불가/없음이면 default, 상한 cap."""
    if not value:
        return default
    v = value.strip()
    try:
        secs = float(v)
        return max(0.0, min(secs, cap))
    except ValueError:
        pass
    try:
        from datetime import UTC, datetime
        from email.utils import parsedate_to_datetime

        dt = parsedate_to_datetime(v)
        delta = (dt - datetime.now(UTC)).total_seconds()
        return max(0.0, min(delta, cap))
    except (TypeError, ValueError, IndexError):
        return default


__all__ = [
    "BlockedAttempt",
    "Origin",
    "OutboundGuard",
    "approved_origins_from_config",
    "assert_origin_allowed",
    "is_loopback_host",
    "is_origin_allowed",
    "make_client",
    "parse_origin",
    "parse_origins",
    "parse_retry_after",
    "reject_redirect",
]
