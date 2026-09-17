"""adapter 공통: 상태 레코드 helper, HTTP 응답 요약, 재시도 정책 상수.

비밀값(토큰/키) 은 어떤 경로로도 details/로그에 넣지 않는다. 응답 본문은 길이 제한된 요약만 남긴다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from corp_dl_agent.common import Status, StatusRecord, now_iso

# HTTP 재시도 정책 (BUILD_SPEC [11]): 401/403 재시도 0, 429/일시 5xx 최대 2
AUTH_STATUSES = frozenset({401, 403})
TRANSIENT_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_TRANSIENT_RETRIES = 2
MAX_RESPONSE_BYTES = 4 * 1024 * 1024  # 응답 본문 상한 (LLM/REST 공통)
BODY_PREVIEW_CHARS = 300


def status_record(status: Status, reason: str, *evidence: str) -> StatusRecord:
    return StatusRecord(status=status, reason=reason, evidence=list(evidence), checked_at=now_iso())


def not_run(reason: str, *evidence: str) -> StatusRecord:
    return status_record(Status.NOT_RUN, reason, *evidence)


def blocked(reason: str, *evidence: str) -> StatusRecord:
    return status_record(Status.BLOCKED, reason, *evidence)


def mock_tested(reason: str, *evidence: str) -> StatusRecord:
    return status_record(Status.MOCK_TESTED, reason, *evidence)


def body_preview(body: bytes | str | None, limit: int = BODY_PREVIEW_CHARS) -> str:
    """응답 본문의 짧은 미리보기 (마스킹은 호출자가 REGISTRY.mask 로 수행)."""
    if body is None:
        return ""
    text = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else str(body)
    text = text.replace("\n", " ").strip()
    return text[:limit] + ("…" if len(text) > limit else "")


@dataclass(frozen=True)
class HttpOutcome:
    """adapter 가 기록하는 1회 HTTP 시도 결과 (비밀값 없음)."""

    attempt: int
    status: int | None
    retried: bool
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "attempt": self.attempt,
            "status": self.status,
            "retried": self.retried,
            "reason": self.reason,
        }


__all__ = [
    "AUTH_STATUSES",
    "BODY_PREVIEW_CHARS",
    "HttpOutcome",
    "MAX_RESPONSE_BYTES",
    "MAX_TRANSIENT_RETRIES",
    "TRANSIENT_STATUSES",
    "blocked",
    "body_preview",
    "mock_tested",
    "not_run",
    "status_record",
]
