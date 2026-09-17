"""Atlassian adapter: Bitbucket / Jira / Confluence / Bamboo 클라이언트 계약 (기본 읽기·preview 만).

정책 (BUILD_SPEC [11])
- Cloud / Data Center 의 base path·인증·pagination 을 구분한다.
    * Jira        Cloud `/rest/api/3` (search/jql 은 nextPageToken cursor) / DC `/rest/api/2` (startAt/maxResults)
    * Confluence  Cloud `/wiki/rest/api` / DC `/rest/api`               (start/limit + _links.next)
    * Bitbucket   Cloud `/2.0` (next URL cursor) / DC `/rest/api/1.0` (start/limit + isLastPage/nextPageStart)
    * Bamboo      DC `/rest/api/latest` (start-index/max-result). Bamboo Cloud 는 제공되지 않으므로 E_NOT_SUPPORTED.
- 인증은 secret 참조(env:/file:) 로만 주입한다. Cloud 는 Basic(secret 값 = "email:api_token"), Data Center 는
  Bearer(PAT) 가 기본이며 auth_scheme 로 명시 변경할 수 있다. 값은 SecretRegistry 에 등록되어 마스킹된다.
- 429 는 Retry-After 를 존중하여 최대 2회 재시도, 일시 5xx 도 최대 2회, 401/403 은 재시도 0.
  409 는 E_INTEGRATION_CONFLICT 로 보고한다 (자동 덮어쓰기 없음).
- 모든 요청 URL(다음 page URL 포함) 은 approved_origins 검사·redirect 거부·TLS 검증 유지·trust_env=False.
- integrations.write=false(기본) 이면 write 메서드는 preview dict 만 만들고, 실제 write 시도(create_*) 는
  E_INTEGRATION_WRITE_DISABLED. write=true 여도 confirm=True(사용자 명시 지시) 없이는 쓰지 않는다.
- doctor() 는 연결하지 않고 설정 상태만 보고한다 (설정 없음 -> NOT_RUN 사유). live verified 를 주장하지 않는다.
- 개인 PC 에서는 mock 서버 계약 시험까지만 가능하다 (MOCK_TESTED).
"""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Literal, Self
from urllib.parse import urlencode, urljoin, urlsplit

from corp_dl_agent.adapters.base import AUTH_STATUSES, MAX_RESPONSE_BYTES, TRANSIENT_STATUSES, body_preview
from corp_dl_agent.common import (
    Status,
    StatusRecord,
    atomic_write_json,
    canonical_json,
    now_iso,
    read_json,
    sha256_text,
)
from corp_dl_agent.config.loader import resolve_secret
from corp_dl_agent.config.schemas import AppConfig, AtlassianProductConfig
from corp_dl_agent.errors import AgentError
from corp_dl_agent.security.network import (
    assert_origin_allowed,
    is_origin_allowed,
    make_client,
    parse_origin,
    parse_retry_after,
    reject_redirect,
)
from corp_dl_agent.security.secrets import REGISTRY

Product = Literal["bitbucket", "jira", "confluence", "bamboo"]
PRODUCTS: tuple[str, ...] = ("bitbucket", "jira", "confluence", "bamboo")
AuthScheme = Literal["basic", "bearer"]
PageStyle = Literal["start_limit", "start_at_max_results", "cursor"]

MAX_TRANSIENT_RETRIES = 2
MAX_PAGES = 200
DEFAULT_PAGE_SIZE = 50
PRODUCT_LABEL_KO: dict[str, str] = {
    "bitbucket": "Bitbucket (코드/PR)",
    "jira": "Jira (요청/실험 이력)",
    "confluence": "Confluence (자료/model card)",
    "bamboo": "Bamboo (시험/job)",
}


# --------------------------------------------------------------------------- pagination 명세


@dataclass(frozen=True)
class PageSpec:
    """응답 page 구조 명세. items_keys 는 후보 키 순서 (예: values / results / issues)."""

    style: PageStyle
    items_keys: tuple[str, ...]
    start_param: str = "start"
    limit_param: str = "limit"
    page_size: int = DEFAULT_PAGE_SIZE
    cursor_param: str = "nextPageToken"  # cursor 값을 query 로 보내는 API (Jira Cloud search/jql)
    total_key: str = "total"
    items_path: tuple[str, ...] = ()  # Bamboo 처럼 {"plans": {"plan": [...]}} 형태의 중첩 경로


@dataclass
class PageResult:
    items: list[Any]
    pages: int
    truncated: bool
    total: int | None = None


def _items_of(data: Any, spec: PageSpec) -> list[Any]:
    node = data
    for key in spec.items_path:
        node = node.get(key, {}) if isinstance(node, dict) else {}
    if not isinstance(node, dict):
        return []
    for key in spec.items_keys:
        val = node.get(key)
        if isinstance(val, list):
            return val
    return []


def _nested(data: Any, spec: PageSpec) -> dict[str, Any]:
    node = data
    for key in spec.items_path:
        node = node.get(key, {}) if isinstance(node, dict) else {}
    return node if isinstance(node, dict) else {}


# --------------------------------------------------------------------------- HTTP 응답 요약


@dataclass(frozen=True)
class AtlassianResponse:
    status: int
    data: Any
    attempts: int
    url: str


class EventLedger:
    """중복 event(webhook/알림) 감지용 원장. event_id 를 기록하고 재수신을 duplicate 로 표시한다.

    path 를 주면 JSON 으로 원자적 저장한다 (append-only 집합). 네트워크는 사용하지 않는다.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else None
        self._seen: set[str] = set()
        if self.path and self.path.is_file():
            data = read_json(self.path)
            if isinstance(data, dict) and isinstance(data.get("seen"), list):
                self._seen = {str(x) for x in data["seen"]}

    @staticmethod
    def event_key(product: str, event: dict[str, Any]) -> str:
        for key in ("id", "event_id", "eventId", "uuid", "delivery_id", "X-Request-Id"):
            v = event.get(key)
            if v not in (None, ""):
                return f"{product}:{v}"
        # id 가 없으면 내용 hash 로 결정적 키를 만든다
        return f"{product}:sha256:{sha256_text(canonical_json(event))[:32]}"

    def is_duplicate(self, key: str) -> bool:
        return key in self._seen

    def record(self, key: str) -> bool:
        """처음이면 True(기록), 이미 있으면 False(duplicate)."""
        if key in self._seen:
            return False
        self._seen.add(key)
        if self.path:
            atomic_write_json(self.path, {"seen": sorted(self._seen), "updated_at": now_iso()})
        return True

    def __len__(self) -> int:
        return len(self._seen)


# --------------------------------------------------------------------------- 공통 client


class AtlassianClient:
    """제품 공통: 설정 검증, 인증 헤더, 요청/재시도/conflict, pagination, preview/write 정책."""

    product: ClassVar[str] = ""
    cloud_base_path: ClassVar[str | None] = None
    datacenter_base_path: ClassVar[str | None] = None
    probe_path: ClassVar[str] = ""  # doctor(probe=True) 용 읽기 전용 endpoint (base path 기준)

    def probe_endpoint(self) -> str:
        return self.probe_path

    def __init__(
        self,
        product_cfg: AtlassianProductConfig,
        write_enabled: bool = False,
        *,
        approved_origins: Iterable[str] = (),
        app_cfg: AppConfig | None = None,
        client: Any | None = None,
        sleep: Callable[[float], None] | None = None,
        auth_scheme: AuthScheme | None = None,
    ) -> None:
        self.cfg = product_cfg
        self.write_enabled = bool(write_enabled)
        self.approved_origins = list(approved_origins)
        self.app_cfg = app_cfg
        self._client = client
        self._owns_client = client is None
        self._sleep: Callable[[float], None] = sleep or (lambda s: time.sleep(min(s, 30.0)))
        self.auth_scheme: AuthScheme = auth_scheme or (
            "basic" if product_cfg.deployment == "cloud" else "bearer"
        )
        self.outcomes: list[dict[str, Any]] = []
        self.requests_made = 0
        self._secret: str | None = None

    @classmethod
    def from_app_config(
        cls,
        cfg: AppConfig,
        *,
        client: Any | None = None,
        sleep: Callable[[float], None] | None = None,
        auth_scheme: AuthScheme | None = None,
    ) -> Self:
        product_cfg: AtlassianProductConfig = getattr(cfg.integrations, cls.product)
        return cls(
            product_cfg,
            cfg.integrations.write,
            approved_origins=cfg.integrations.approved_origins,
            app_cfg=cfg,
            client=client,
            sleep=sleep,
            auth_scheme=auth_scheme,
        )

    # ------------------------------------------------------------------ 설정/경로
    @property
    def label_ko(self) -> str:
        return PRODUCT_LABEL_KO.get(self.product, self.product)

    def missing_fields(self) -> list[str]:
        missing: list[str] = []
        if not self.cfg.base_url:
            missing.append("base_url")
        if not self.cfg.secret_ref:
            missing.append("secret_ref")
        if not self.approved_origins:
            missing.append("integrations.approved_origins")
        return missing

    def base_path(self) -> str:
        path = self.cloud_base_path if self.cfg.deployment == "cloud" else self.datacenter_base_path
        if path is None:
            raise AgentError(
                "E_NOT_SUPPORTED",
                f"{self.label_ko}: deployment '{self.cfg.deployment}' 는 지원되지 않습니다",
                details={"product": self.product, "deployment": self.cfg.deployment},
            )
        if self.cfg.api_version:
            path = path.replace("{version}", self.cfg.api_version)
        return path

    def _require_config(self) -> str:
        missing = self.missing_fields()
        if missing:
            raise AgentError(
                "E_BLOCKED_CONFIG",
                f"{self.label_ko} 설정이 불완전합니다 (public/기본 주소로 fallback 하지 않습니다)",
                details={"product": self.product, "missing": missing},
            )
        assert self.cfg.base_url
        return self.cfg.base_url.rstrip("/")

    def url(self, path: str) -> str:
        """base_url + base path + path. 절대 URL 이면 그대로(origin 검사는 호출 시)."""
        if path.startswith("http://") or path.startswith("https://"):
            return path
        base = self._require_config()
        rel = path if path.startswith("/") else "/" + path
        return base + self.base_path() + rel

    # ------------------------------------------------------------------ 인증
    def _resolve_secret(self) -> str:
        if self._secret is None:
            secret = resolve_secret(self.cfg.secret_ref)
            REGISTRY.register(secret)
            self._secret = secret
        return self._secret

    def auth_headers(self) -> dict[str, str]:
        secret = self._resolve_secret()
        if self.auth_scheme == "basic":
            if ":" not in secret:
                raise AgentError(
                    "E_BLOCKED_CONFIG",
                    f"{self.label_ko}: Basic 인증 secret 값은 'email:api_token' 형식이어야 합니다 (값은 표시하지 않음)",
                    details={"product": self.product, "auth_scheme": "basic"},
                )
            token = base64.b64encode(secret.encode("utf-8")).decode("ascii")
            REGISTRY.register(token)
            return {"Authorization": f"Basic {token}"}
        return {"Authorization": f"Bearer {secret}"}

    def headers(self) -> dict[str, str]:
        return {**self.auth_headers(), "Accept": "application/json", "Content-Type": "application/json"}

    # ------------------------------------------------------------------ HTTP
    def _http(self) -> Any:
        if self._client is None:
            if self.app_cfg is None:
                raise AgentError(
                    "E_NETWORK_BLOCKED",
                    f"{self.label_ko}: 네트워크 허용 설정(AppConfig) 없이 연결하지 않습니다",
                )
            self._client = make_client(self.app_cfg, self.cfg.ca_bundle, timeout=self.cfg.timeout_seconds)
        return self._client

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            try:
                self._client.close()
            finally:
                self._client = None

    def __enter__(self) -> AtlassianClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _record(self, attempt: int, status: int | None, retried: bool, reason: str) -> None:
        self.outcomes.append(
            {
                "product": self.product,
                "attempt": attempt,
                "status": status,
                "retried": retried,
                "reason": REGISTRY.mask(reason),
                "at": now_iso(),
            }
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any | None = None,
        expect: tuple[int, ...] = (200,),
    ) -> AtlassianResponse:
        """1회 요청 (재시도 포함). 모든 URL 은 approved_origins 검사를 통과해야 한다."""
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - httpx 는 core 의존성
            from corp_dl_agent.errors import blocked_dependency

            raise blocked_dependency("httpx", "atlassian.request") from exc

        url = self.url(path)
        assert_origin_allowed(url, self.approved_origins, context=f"{self.product} request")
        headers = self.headers()
        max_attempts = 1 + MAX_TRANSIENT_RETRIES
        for attempt in range(1, max_attempts + 1):
            self.requests_made += 1
            try:
                resp = self._http().request(
                    method.upper(), url, params=params, json=json_body, headers=headers
                )
            except httpx.TimeoutException as exc:
                if attempt < max_attempts:
                    self._record(attempt, None, True, f"timeout: {type(exc).__name__}")
                    self._sleep(1.0)
                    continue
                self._record(attempt, None, False, f"timeout: {type(exc).__name__}")
                raise AgentError(
                    "E_GATEWAY_RESPONSE",
                    f"{self.label_ko} 응답 시간 초과 ({self.cfg.timeout_seconds}s, {attempt}회 시도)",
                    details={"product": self.product, "attempts": attempt},
                ) from exc
            except httpx.HTTPError as exc:
                self._record(attempt, None, False, f"transport: {type(exc).__name__}")
                raise AgentError(
                    "E_GATEWAY_RESPONSE",
                    f"{self.label_ko} 연결 실패: {type(exc).__name__}",
                    details={"product": self.product, "attempts": attempt},
                ) from exc

            status = int(resp.status_code)
            if 300 <= status < 400:
                self._record(attempt, status, False, "redirect rejected")
                reject_redirect(status, resp.headers.get("location"), context=f"{self.product}")
            if status in AUTH_STATUSES:
                self._record(attempt, status, False, "auth/permission error (no retry)")
                raise AgentError(
                    "E_GATEWAY_AUTH",
                    f"{self.label_ko} 인증/권한 오류 (HTTP {status}). 재시도하지 않습니다",
                    details={"product": self.product, "status": status, "attempts": attempt},
                )
            if status == 409:
                self._record(attempt, status, False, "conflict")
                raise AgentError(
                    "E_INTEGRATION_CONFLICT",
                    f"{self.label_ko}: 원격 리소스 충돌 (HTTP 409)",
                    details={
                        "product": self.product,
                        "status": 409,
                        "method": method.upper(),
                        "path": urlsplit(url).path,
                        "body_preview": REGISTRY.mask(body_preview(resp.content)),
                    },
                )
            if status in TRANSIENT_STATUSES:
                if attempt < max_attempts:
                    wait = parse_retry_after(resp.headers.get("retry-after"), default=1.0)
                    kind = "rate limit" if status == 429 else "transient"
                    self._record(attempt, status, True, f"{kind}, retry after {wait}s")
                    self._sleep(wait)
                    continue
                self._record(attempt, status, False, "transient, retries exhausted")
                raise AgentError(
                    "E_GATEWAY_RESPONSE",
                    f"{self.label_ko} {'rate limit(429)' if status == 429 else f'일시 오류(HTTP {status})'} 가 "
                    f"{attempt}회 반복되어 중단합니다",
                    details={"product": self.product, "status": status, "attempts": attempt},
                )
            if status not in expect:
                self._record(attempt, status, False, "unexpected status")
                raise AgentError(
                    "E_GATEWAY_RESPONSE",
                    f"{self.label_ko} 가 예상하지 않은 상태를 반환했습니다 (HTTP {status})",
                    details={
                        "product": self.product,
                        "status": status,
                        "body_preview": REGISTRY.mask(body_preview(resp.content)),
                    },
                )
            content = resp.content
            if len(content) > MAX_RESPONSE_BYTES:
                self._record(attempt, status, False, "response too large")
                raise AgentError(
                    "E_GATEWAY_RESPONSE",
                    f"{self.label_ko} 응답이 너무 큽니다",
                    details={"bytes": len(content)},
                )
            data: Any = None
            if content:
                try:
                    data = json.loads(content.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    self._record(attempt, status, False, "invalid json body")
                    raise AgentError(
                        "E_GATEWAY_RESPONSE",
                        f"{self.label_ko} 응답 본문이 JSON 이 아닙니다",
                        details={"body_preview": REGISTRY.mask(body_preview(content))},
                    ) from exc
            self._record(attempt, status, False, "ok")
            return AtlassianResponse(status=status, data=data, attempts=attempt, url=url)
        raise AgentError("E_INTERNAL", "요청 루프가 결과 없이 종료되었습니다")  # pragma: no cover

    def get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        return self.request("GET", path, params=params).data

    # ------------------------------------------------------------------ pagination
    def iter_pages(
        self,
        path: str,
        spec: PageSpec,
        *,
        params: dict[str, Any] | None = None,
        max_pages: int = MAX_PAGES,
    ) -> Iterator[tuple[list[Any], Any]]:
        """page 단위 iterator: (items, raw_page). 세 방식(start/limit, startAt/maxResults, cursor) 을 다룬다."""
        base_params: dict[str, Any] = dict(params or {})
        pages = 0
        next_url: str | None = None
        start = int(base_params.pop(spec.start_param, 0) or 0)
        cursor: str | None = None
        while pages < max_pages:
            if next_url is not None:
                # cursor(next URL): 절대/상대 URL 모두 origin 검사. query 는 URL 에 이미 포함.
                data = self.get(next_url)
            else:
                page_params = dict(base_params)
                if spec.style in ("start_limit", "start_at_max_results"):
                    page_params[spec.start_param] = start
                    page_params[spec.limit_param] = spec.page_size
                elif cursor is not None:
                    page_params[spec.cursor_param] = cursor
                    page_params.setdefault(spec.limit_param, spec.page_size)
                else:
                    page_params.setdefault(spec.limit_param, spec.page_size)
                data = self.get(path, params=page_params)
            pages += 1
            items = _items_of(data, spec)
            yield items, data
            node = _nested(data, spec)
            if spec.style == "start_at_max_results":
                total = node.get(spec.total_key)
                got = int(node.get(spec.start_param, start)) + len(items)
                if (
                    not items
                    or (isinstance(total, int) and got >= total)
                    or (total is None and len(items) < spec.page_size)
                ):
                    return
                start = got
                continue
            if spec.style == "start_limit":
                if not items:
                    return
                is_last = node.get("isLastPage")
                if is_last is True:
                    return
                if is_last is False and isinstance(node.get("nextPageStart"), int):
                    start = int(node["nextPageStart"])
                    continue
                links = node.get("_links") if isinstance(node.get("_links"), dict) else {}
                nxt = links.get("next") if isinstance(links, dict) else None
                if isinstance(nxt, str) and nxt:
                    next_url = self._resolve_next(nxt)
                    continue
                size = node.get("size")
                limit = node.get(spec.limit_param, spec.page_size)
                if isinstance(size, int) and isinstance(limit, int) and size < limit:
                    return
                if len(items) < spec.page_size and not isinstance(size, int):
                    return
                start = int(node.get(spec.start_param, start)) + len(items)
                continue
            # cursor
            if not items:
                return
            nxt_val = node.get("next")
            if isinstance(nxt_val, str) and nxt_val.startswith(("http://", "https://", "/")):
                next_url = self._resolve_next(nxt_val)
                continue
            links = node.get("_links") if isinstance(node.get("_links"), dict) else {}
            link_next = links.get("next") if isinstance(links, dict) else None
            if isinstance(link_next, str) and link_next:
                next_url = self._resolve_next(link_next)
                continue
            token = node.get(spec.cursor_param) or node.get("nextPageToken") or node.get("cursor")
            if isinstance(token, str) and token:
                next_url = None
                cursor = token
                if node.get("isLast") is True:
                    return
                continue
            return
        # max_pages 도달: 호출자가 truncated 로 표시한다

    def _resolve_next(self, link: str) -> str:
        """다음 page 링크를 절대 URL 로 만들고 approved origin 검사. 다른 origin 이면 E_ORIGIN_NOT_ALLOWED."""
        base = self._require_config() + "/"
        absolute = link if link.startswith(("http://", "https://")) else urljoin(base, link.lstrip("/"))
        assert_origin_allowed(absolute, self.approved_origins, context=f"{self.product} pagination next")
        return absolute

    def collect(
        self,
        path: str,
        spec: PageSpec,
        *,
        params: dict[str, Any] | None = None,
        max_items: int = 1000,
        max_pages: int = MAX_PAGES,
    ) -> PageResult:
        items: list[Any] = []
        pages = 0
        truncated = False
        total: int | None = None
        for page_items, raw in self.iter_pages(path, spec, params=params, max_pages=max_pages + 1):
            pages += 1
            if pages > max_pages:
                pages = max_pages
                truncated = True
                break
            node = _nested(raw, spec)
            if total is None and isinstance(node.get(spec.total_key), int):
                total = int(node[spec.total_key])
            if len(items) + len(page_items) > max_items:
                items.extend(page_items[: max_items - len(items)])
                truncated = True
                break
            items.extend(page_items)
        return PageResult(items=items, pages=pages, truncated=truncated, total=total)

    # ------------------------------------------------------------------ write 정책
    def _preview(self, action: str, method: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
        """실제 요청 없이 '보낼 요청' 을 dict 로 만든다 (secret 없음). base_url 이 없어도 preview 는 가능."""
        base = (self.cfg.base_url or "<base_url 미설정>").rstrip("/")
        try:
            base_path = self.base_path()
        except AgentError as exc:
            base_path = f"<{exc.code}>"
        return {
            "product": self.product,
            "deployment": self.cfg.deployment,
            "action": action,
            "method": method.upper(),
            "url": base + base_path + path,
            "path": path,
            "body": body,
            "write_enabled": self.write_enabled,
            "would_write": True,
            "performed": False,
            "note": (
                "integrations.write=false: preview/export 만 제공합니다"
                if not self.write_enabled
                else "write=true 이지만 confirm=True(사용자 명시 지시) 없이는 실행하지 않습니다"
            ),
            "created_at": now_iso(),
        }

    def _write(self, preview: dict[str, Any], *, confirm: bool, expect: tuple[int, ...]) -> dict[str, Any]:
        if not self.write_enabled:
            raise AgentError(
                "E_INTEGRATION_WRITE_DISABLED",
                f"{self.label_ko}: {preview['action']} 은 integrations.write=false 이므로 수행하지 않습니다 (preview 만 제공)",
                details={"product": self.product, "preview": preview},
            )
        if not confirm:
            raise AgentError(
                "E_INTEGRATION_WRITE_DISABLED",
                f"{self.label_ko}: {preview['action']} 은 사용자 명시 지시(confirm=True) 없이는 수행하지 않습니다",
                details={"product": self.product, "preview": preview},
            )
        resp = self.request(preview["method"], preview["path"], json_body=preview["body"], expect=expect)
        return {**preview, "performed": True, "status": resp.status, "response": resp.data}

    # ------------------------------------------------------------------ doctor
    def doctor(self, *, probe: bool = False) -> StatusRecord:
        """설정 상태 보고. probe=False(기본) 면 연결하지 않으며 live verified 를 주장하지 않는다."""
        checked = now_iso()
        if not self.cfg.enabled:
            return StatusRecord(
                status=Status.NOT_RUN,
                reason=f"{self.label_ko}: 설정 없음 (enabled=false). 코어는 이 제품 없이 CLI 로 동작합니다",
                checked_at=checked,
            )
        missing = self.missing_fields()
        if missing:
            return StatusRecord(
                status=Status.BLOCKED,
                reason=f"{self.label_ko}: 필수 설정 누락: {', '.join(missing)}",
                checked_at=checked,
            )
        assert self.cfg.base_url
        try:
            self.base_path()
        except AgentError as exc:
            return StatusRecord(
                status=Status.BLOCKED, reason=f"{self.label_ko}: {exc.message}", checked_at=checked
            )
        try:
            origin = parse_origin(self.cfg.base_url)
        except AgentError as exc:
            return StatusRecord(
                status=Status.BLOCKED, reason=f"{self.label_ko}: {exc.message}", checked_at=checked
            )
        if not is_origin_allowed(self.cfg.base_url, self.approved_origins):
            return StatusRecord(
                status=Status.BLOCKED,
                reason=f"{self.label_ko}: base_url origin 이 integrations.approved_origins 에 없습니다 ({origin})",
                checked_at=checked,
            )
        if self.app_cfg is not None and not self.app_cfg.network_allowed():
            return StatusRecord(
                status=Status.BLOCKED,
                reason=f"{self.label_ko}: 프로파일 {self.app_cfg.profile.value} 에서는 네트워크 호출이 허용되지 않습니다",
                checked_at=checked,
            )
        evidence = [
            f"deployment={self.cfg.deployment}",
            f"base_path={self.base_path()}",
            f"auth_scheme={self.auth_scheme}",
            "secret_ref=***",
            f"write_enabled={self.write_enabled}",
        ]
        if not probe:
            return StatusRecord(
                status=Status.NOT_RUN,
                reason=f"{self.label_ko}: 설정은 있으나 live 연결은 검증되지 않았습니다 (probe 미실행)",
                evidence=evidence,
                checked_at=checked,
            )
        try:
            resp = self.request("GET", self.probe_endpoint())
        except AgentError as exc:
            return StatusRecord(
                status=Status.FAIL,
                reason=f"{self.label_ko}: probe 실패 {exc.code}: {exc.message}",
                evidence=evidence,
                checked_at=checked,
            )
        if origin.is_loopback:
            return StatusRecord(
                status=Status.MOCK_TESTED,
                reason=f"{self.label_ko}: loopback mock 서버 probe 응답 (HTTP {resp.status}). 사내 live 검증 아님",
                evidence=[*evidence, f"probe_status={resp.status}"],
                checked_at=checked,
            )
        return StatusRecord(
            status=Status.PASS,
            reason=f"{self.label_ko}: probe 응답 (HTTP {resp.status}). 연결 확인일 뿐 업무 통합 검증(CORP_INTEGRATED) 아님",
            evidence=[*evidence, f"probe_status={resp.status}"],
            checked_at=checked,
        )


# --------------------------------------------------------------------------- 제품별 client


class JiraClient(AtlassianClient):
    product: ClassVar[str] = "jira"
    cloud_base_path: ClassVar[str | None] = "/rest/api/3"
    datacenter_base_path: ClassVar[str | None] = "/rest/api/2"
    probe_path: ClassVar[str] = "/myself"

    SEARCH_DC: ClassVar[PageSpec] = PageSpec(
        style="start_at_max_results", items_keys=("issues",), start_param="startAt", limit_param="maxResults"
    )
    SEARCH_CLOUD: ClassVar[PageSpec] = PageSpec(
        style="cursor", items_keys=("issues",), limit_param="maxResults", cursor_param="nextPageToken"
    )

    def get_issue(self, key: str) -> Any:
        return self.get(f"/issue/{key}")

    def search_issues(
        self, jql: str, *, fields: Iterable[str] = ("summary", "status"), max_items: int = 500
    ) -> PageResult:
        params: dict[str, Any] = {"jql": jql, "fields": ",".join(fields)}
        if self.cfg.deployment == "cloud":
            return self.collect("/search/jql", self.SEARCH_CLOUD, params=params, max_items=max_items)
        return self.collect("/search", self.SEARCH_DC, params=params, max_items=max_items)

    def preview_issue(self, payload: dict[str, Any]) -> dict[str, Any]:
        """issue 생성 preview. payload: {summary, description?, issue_type?, project_key?, labels?}."""
        summary = str(payload.get("summary", "")).strip()
        if not summary:
            raise AgentError("E_INPUT_INVALID", "issue preview 에는 summary 가 필요합니다")
        project_key = payload.get("project_key") or self.cfg.project_key
        fields: dict[str, Any] = {
            "project": {"key": project_key} if project_key else "MISSING(project_key)",
            "summary": summary,
            "issuetype": {"name": str(payload.get("issue_type", "Task"))},
        }
        if payload.get("description"):
            fields["description"] = str(payload["description"])
        if payload.get("labels"):
            fields["labels"] = [str(x) for x in payload["labels"]]
        return self._preview("create_issue", "POST", "/issue", {"fields": fields})

    def create_issue(self, payload: dict[str, Any], *, confirm: bool = False) -> dict[str, Any]:
        return self._write(self.preview_issue(payload), confirm=confirm, expect=(200, 201))


class ConfluenceClient(AtlassianClient):
    product: ClassVar[str] = "confluence"
    cloud_base_path: ClassVar[str | None] = "/wiki/rest/api"
    datacenter_base_path: ClassVar[str | None] = "/rest/api"
    probe_path: ClassVar[str] = "/user/current"

    CONTENT: ClassVar[PageSpec] = PageSpec(
        style="start_limit", items_keys=("results",), start_param="start", limit_param="limit"
    )

    def get_page(self, page_id: str) -> Any:
        return self.get(f"/content/{page_id}", params={"expand": "version,space"})

    def list_pages(self, space_key: str | None = None, *, max_items: int = 500) -> PageResult:
        key = space_key or self.cfg.space_key
        if not key:
            raise AgentError("E_BLOCKED_CONFIG", "Confluence space_key 가 설정되어 있지 않습니다")
        return self.collect(
            "/content", self.CONTENT, params={"spaceKey": key, "type": "page"}, max_items=max_items
        )

    def preview_page(self, payload: dict[str, Any]) -> dict[str, Any]:
        """page 생성 preview. payload: {title, body_storage, space_key?, parent_id?}."""
        title = str(payload.get("title", "")).strip()
        if not title:
            raise AgentError("E_INPUT_INVALID", "page preview 에는 title 이 필요합니다")
        space_key = payload.get("space_key") or self.cfg.space_key
        body: dict[str, Any] = {
            "type": "page",
            "title": title,
            "space": {"key": space_key} if space_key else "MISSING(space_key)",
            "body": {"storage": {"value": str(payload.get("body_storage", "")), "representation": "storage"}},
        }
        if payload.get("parent_id"):
            body["ancestors"] = [{"id": str(payload["parent_id"])}]
        return self._preview("create_page", "POST", "/content", body)

    def create_page(self, payload: dict[str, Any], *, confirm: bool = False) -> dict[str, Any]:
        return self._write(self.preview_page(payload), confirm=confirm, expect=(200,))


class BitbucketClient(AtlassianClient):
    product: ClassVar[str] = "bitbucket"
    cloud_base_path: ClassVar[str | None] = "/2.0"
    datacenter_base_path: ClassVar[str | None] = "/rest/api/1.0"
    probe_path: ClassVar[str] = "/user"

    PR_DC: ClassVar[PageSpec] = PageSpec(
        style="start_limit", items_keys=("values",), start_param="start", limit_param="limit"
    )
    PR_CLOUD: ClassVar[PageSpec] = PageSpec(style="cursor", items_keys=("values",), limit_param="pagelen")

    def probe_endpoint(self) -> str:
        return "/user" if self.cfg.deployment == "cloud" else "/application-properties"

    def _repo_path(self, project_or_workspace: str | None, repo_slug: str | None) -> tuple[str, str]:
        slug = repo_slug or self.cfg.repo_slug
        proj = project_or_workspace or self.cfg.project_key
        if not (slug and proj):
            raise AgentError(
                "E_BLOCKED_CONFIG",
                "Bitbucket project(workspace) 키와 repo_slug 가 필요합니다",
                details={"project_key": bool(proj), "repo_slug": bool(slug)},
            )
        return proj, slug

    def list_pull_requests(
        self,
        project: str | None = None,
        repo_slug: str | None = None,
        *,
        state: str = "OPEN",
        max_items: int = 500,
    ) -> PageResult:
        proj, slug = self._repo_path(project, repo_slug)
        if self.cfg.deployment == "cloud":
            return self.collect(
                f"/repositories/{proj}/{slug}/pullrequests",
                self.PR_CLOUD,
                params={"state": state.upper()},
                max_items=max_items,
            )
        return self.collect(
            f"/projects/{proj}/repos/{slug}/pull-requests",
            self.PR_DC,
            params={"state": state.upper()},
            max_items=max_items,
        )

    def preview_pr(self, payload: dict[str, Any]) -> dict[str, Any]:
        """pull request 생성 preview. payload: {title, source_branch, target_branch, description?, project?, repo_slug?}."""
        title = str(payload.get("title", "")).strip()
        src = str(payload.get("source_branch", "")).strip()
        dst = str(payload.get("target_branch", "")).strip()
        if not (title and src and dst):
            raise AgentError(
                "E_INPUT_INVALID", "PR preview 에는 title, source_branch, target_branch 가 필요합니다"
            )
        proj, slug = self._repo_path(payload.get("project"), payload.get("repo_slug"))
        if self.cfg.deployment == "cloud":
            body: dict[str, Any] = {
                "title": title,
                "description": str(payload.get("description", "")),
                "source": {"branch": {"name": src}},
                "destination": {"branch": {"name": dst}},
            }
            return self._preview(
                "create_pull_request", "POST", f"/repositories/{proj}/{slug}/pullrequests", body
            )
        body = {
            "title": title,
            "description": str(payload.get("description", "")),
            "fromRef": {"id": f"refs/heads/{src}", "repository": {"slug": slug, "project": {"key": proj}}},
            "toRef": {"id": f"refs/heads/{dst}", "repository": {"slug": slug, "project": {"key": proj}}},
        }
        return self._preview(
            "create_pull_request", "POST", f"/projects/{proj}/repos/{slug}/pull-requests", body
        )

    def create_pr(self, payload: dict[str, Any], *, confirm: bool = False) -> dict[str, Any]:
        return self._write(self.preview_pr(payload), confirm=confirm, expect=(200, 201))


class BambooClient(AtlassianClient):
    product: ClassVar[str] = "bamboo"
    cloud_base_path: ClassVar[str | None] = None  # Bamboo Cloud 는 제공되지 않는다 (Data Center 전용)
    datacenter_base_path: ClassVar[str | None] = "/rest/api/latest"
    probe_path: ClassVar[str] = "/info"

    PLANS: ClassVar[PageSpec] = PageSpec(
        style="start_limit",
        items_keys=("plan",),
        start_param="start-index",
        limit_param="max-result",
        items_path=("plans",),
        page_size=25,
    )

    def list_plans(self, *, max_items: int = 500) -> PageResult:
        return self.collect("/plan", self.PLANS, max_items=max_items)

    def preview_build(self, payload: dict[str, Any]) -> dict[str, Any]:
        """build queue preview. payload: {plan_key, variables?: {name: value}}."""
        plan_key = str(payload.get("plan_key", "")).strip()
        if not plan_key:
            raise AgentError("E_INPUT_INVALID", "build preview 에는 plan_key 가 필요합니다")
        variables = payload.get("variables") or {}
        if not isinstance(variables, dict):
            raise AgentError("E_INPUT_INVALID", "variables 는 객체(dict) 이어야 합니다")
        var_map: dict[str, str] = {str(k): str(v) for k, v in variables.items()}
        body: dict[str, Any] = {"stage": "", "executeAllStages": True, "variables": var_map}
        path = f"/queue/{plan_key}"
        if var_map:
            path += "?" + urlencode({f"bamboo.variable.{k}": v for k, v in var_map.items()})
        return self._preview("queue_build", "POST", path, body)

    def queue_build(self, payload: dict[str, Any], *, confirm: bool = False) -> dict[str, Any]:
        return self._write(self.preview_build(payload), confirm=confirm, expect=(200,))


CLIENTS: dict[str, type[AtlassianClient]] = {
    "bitbucket": BitbucketClient,
    "jira": JiraClient,
    "confluence": ConfluenceClient,
    "bamboo": BambooClient,
}

PREVIEW_METHODS: dict[str, str] = {
    "jira": "preview_issue",
    "confluence": "preview_page",
    "bitbucket": "preview_pr",
    "bamboo": "preview_build",
}


def client_for(product: str, cfg: AppConfig, **kwargs: Any) -> AtlassianClient:
    cls = CLIENTS.get(product)
    if cls is None:
        raise AgentError(
            "E_USAGE", f"알 수 없는 Atlassian 제품: {product}", details={"allowed": list(PRODUCTS)}
        )
    return cls.from_app_config(cfg, **kwargs)


def preview_payload(product: str, cfg: AppConfig, payload: dict[str, Any]) -> dict[str, Any]:
    """CLI `integrations preview`: 네트워크 없이 preview dict 만 만든다."""
    client = client_for(product, cfg)
    method = getattr(client, PREVIEW_METHODS[product])
    result: dict[str, Any] = method(payload)
    return result


def integrations_doctor(cfg: AppConfig, *, probe: bool = False) -> dict[str, Any]:
    """제품별 StatusRecord + 정책 요약. 기본은 연결하지 않는다."""
    products: dict[str, Any] = {}
    for name in PRODUCTS:
        try:
            rec = client_for(name, cfg).doctor(probe=probe)
        except AgentError as exc:
            rec = StatusRecord(
                status=Status.BLOCKED, reason=f"{exc.code}: {exc.message}", checked_at=now_iso()
            )
        products[name] = rec.model_dump(mode="json")
    return {
        "write_enabled": cfg.integrations.write,
        "approved_origins": list(cfg.integrations.approved_origins),
        "network_allowed": cfg.network_allowed(),
        "profile": cfg.profile.value,
        "products": products,
        "live_verified": False,
        "note": "개인 개발 단계: mock 계약 시험까지만 (MOCK_TESTED/NOT_RUN). live 검증은 사내에서 수행",
    }


__all__ = [
    "CLIENTS",
    "MAX_PAGES",
    "MAX_TRANSIENT_RETRIES",
    "PREVIEW_METHODS",
    "PRODUCTS",
    "AtlassianClient",
    "AtlassianResponse",
    "BambooClient",
    "BitbucketClient",
    "ConfluenceClient",
    "EventLedger",
    "JiraClient",
    "PageResult",
    "PageSpec",
    "client_for",
    "integrations_doctor",
    "preview_payload",
]
