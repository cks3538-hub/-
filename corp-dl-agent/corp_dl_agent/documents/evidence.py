"""evidence.json: 검색 결과(Hit) 를 인용 가능한 근거 항목으로 고정한다.

각 항목은 source_id / sha256 / locator / quoted_text / kind(form_reference|fact) / revision / approval_state 를 가진다.
숨김 콘텐츠는 allow_hidden=True 로 명시하지 않으면 제외하고 excluded_hidden 에 개수만 남긴다 (LLM 입력·산출물 제외).
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

from pydantic import Field

from corp_dl_agent.common import (
    StrictModel,
    atomic_write_json,
    now_iso,
    read_json,
    stable_id,
    validate_strict,
)
from corp_dl_agent.documents.index import DocKind
from corp_dl_agent.documents.search import Hit
from corp_dl_agent.version import SCHEMA_VERSION

MAX_QUOTE_CHARS = 500


class EvidenceItem(StrictModel):
    evidence_id: str
    source_id: str
    path: str
    sha256: str
    locator: str
    quoted_text: str
    kind: DocKind
    revision: str
    approval_state: str
    hidden: bool = False
    fragment_kind: str = ""
    formula: str | None = None
    cached_value: str | None = None
    unit: str | None = None
    base_date: str | None = None
    score: float = 0.0
    synthetic: bool = False
    truncated: bool = False


class Evidence(StrictModel):
    schema_version: str = SCHEMA_VERSION
    purpose: str
    query: str | None = None
    created_at: str
    items: list[EvidenceItem] = Field(default_factory=list)
    n_items: int = 0
    n_fact: int = 0
    n_form_reference: int = 0
    excluded_hidden: int = 0
    synthetic: bool = False
    notes: list[str] = Field(default_factory=list)


def build_evidence(
    hits: Iterable[Hit],
    *,
    purpose: str,
    query: str | None = None,
    allow_hidden: bool = False,
    max_quote_chars: int = MAX_QUOTE_CHARS,
) -> Evidence:
    items: list[EvidenceItem] = []
    excluded_hidden = 0
    seen: set[str] = set()
    for h in hits:
        if h.hidden and not allow_hidden:
            excluded_hidden += 1
            continue
        eid = stable_id("ev", h.source_id, h.locator, h.sha256)
        if eid in seen:
            continue
        seen.add(eid)
        quoted = h.text
        truncated = False
        if len(quoted) > max_quote_chars:
            quoted = quoted[:max_quote_chars] + "…"
            truncated = True
        items.append(
            EvidenceItem(
                evidence_id=eid,
                source_id=h.source_id,
                path=h.path,
                sha256=h.sha256,
                locator=h.locator,
                quoted_text=quoted,
                kind=h.kind,
                revision=h.revision,
                approval_state=h.approval_state,
                hidden=h.hidden,
                fragment_kind=h.fragment_kind,
                formula=h.formula,
                cached_value=h.cached_value,
                unit=h.unit,
                base_date=h.base_date,
                score=h.score,
                synthetic=h.synthetic,
                truncated=truncated,
            )
        )
    notes: list[str] = []
    if excluded_hidden:
        notes.append(f"숨김 콘텐츠 {excluded_hidden}건은 명시적 허용이 없어 근거에서 제외했습니다.")
    n_form = sum(1 for i in items if i.kind == "form_reference")
    if n_form:
        notes.append("form_reference 항목은 과거 양식 참고이며 현재 사실 근거(fact)로 사용하지 않습니다.")
    if not items:
        notes.append("근거 항목이 없습니다. 수치는 report_payload 의 계산/예측 출처만 사용됩니다.")
    synthetic = bool(items) and all(i.synthetic for i in items)
    if synthetic:
        notes.append("모든 근거가 합성(synthetic) 문서에서 나왔습니다.")
    return Evidence(
        purpose=purpose,
        query=query,
        created_at=now_iso(),
        items=items,
        n_items=len(items),
        n_fact=len(items) - n_form,
        n_form_reference=n_form,
        excluded_hidden=excluded_hidden,
        synthetic=synthetic,
        notes=notes,
    )


def write_evidence(evidence: Evidence, path: str | os.PathLike[str]) -> Path:
    p = Path(path)
    atomic_write_json(p, evidence.model_dump(mode="json"))
    return p


def load_evidence(path: str | os.PathLike[str]) -> Evidence:
    data = read_json(path)
    ev: Evidence = validate_strict(Evidence, data)
    return ev
