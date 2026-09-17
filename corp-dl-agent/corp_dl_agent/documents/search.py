"""키워드/metadata 검색 (embedding·OCR 없음).

- 질의는 공백으로 나눈 토큰(따옴표로 구절 지정 가능)의 OR 후보 조회 + 파이썬 점수화.
- hidden fragment(숨김 slide/shape/sheet/행/열) 는 include_hidden=True 로 명시하지 않으면 제외한다.
- 결과 kind: 문서 분류 form_reference(과거 양식) / fact(현재 사실 근거). placeholder 를 포함한 fragment 는 항상 form_reference.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import Field

from corp_dl_agent.common import StrictModel
from corp_dl_agent.documents.index import PLACEHOLDER_MARK, DocKind, DocumentIndex, DocumentMeta, Fragment

_PHRASE_RE = re.compile(r'"([^"]+)"|(\S+)')


class Hit(StrictModel):
    source_id: str
    path: str
    locator: str
    text: str
    score: float
    hidden: bool
    kind: DocKind
    revision: str
    approval_state: str
    fragment_kind: str
    sha256: str
    formula: str | None = None
    cached_value: str | None = None
    unit: str | None = None
    base_date: str | None = None
    synthetic: bool = False
    matched_terms: list[str] = Field(default_factory=list)


def tokenize(query: str) -> list[str]:
    """따옴표 구절은 하나의 토큰. 소문자화. 빈 토큰 제거."""
    tokens: list[str] = []
    for m in _PHRASE_RE.finditer(query or ""):
        tok = (m.group(1) or m.group(2) or "").strip().lower()
        if tok:
            tokens.append(tok)
    return tokens


def _variants(tok: str) -> list[str]:
    out = [tok]
    no_comma = re.sub(r"(?<=\d),(?=\d{3})", "", tok)
    if no_comma != tok:
        out.append(no_comma)
    return out


def score_fragment(tokens: list[str], doc: DocumentMeta, frag: Fragment, query: str) -> tuple[float, list[str]]:
    text_low = frag.text.lower()
    text_nc = re.sub(r"(?<=\d),(?=\d{3})", "", text_low)
    meta_low = " ".join([doc.path.lower(), doc.title.lower(), doc.revision.lower()])
    score = 0.0
    matched: list[str] = []
    for tok in tokens:
        hit = False
        for v in _variants(tok):
            if v in text_low or v in text_nc:
                hit = True
                score += 1.0
                if re.search(rf"(?<![\w가-힣]){re.escape(v)}(?![\w가-힣])", text_low) or re.search(
                    rf"(?<![\w가-힣]){re.escape(v)}(?![\w가-힣])", text_nc
                ):
                    score += 0.1
                break
        if hit:
            matched.append(tok)
        elif tok in meta_low:
            score += 0.3
            matched.append(f"meta:{tok}")
    if not any(not m.startswith("meta:") for m in matched):
        return 0.0, matched
    q = query.strip().lower()
    if len(tokens) > 1 and q and (q in text_low or q in text_nc):
        score += 0.5
    if frag.fragment_kind in ("named_range", "chart_series", "table_cell", "xl_table_cell"):
        score += 0.05
    if doc.approval_state == "approved":
        score += 0.05
    return round(score, 4), matched


_LOCATOR_PRIORITY = ("range:", "table:", "sheet:", "slide:")


def _priority(locator: str) -> int:
    for i, p in enumerate(_LOCATOR_PRIORITY):
        if locator.startswith(p):
            return i
    return len(_LOCATOR_PRIORITY)


def search(
    index: DocumentIndex,
    query: str,
    *,
    limit: int = 20,
    include_hidden: bool = False,
    kind: Literal["form_reference", "fact"] | None = None,
) -> list[Hit]:
    """키워드 검색. 반환은 score 내림차순, 동점이면 hidden 아님 > 경로 > locator 순(결정적)."""
    tokens = tokenize(query)
    if not tokens:
        return []
    patterns: list[str] = []
    for tok in tokens:
        patterns.extend(_variants(tok))
    candidates = index.candidate_fragments(patterns, include_hidden=include_hidden)
    scored: list[Hit] = []
    for doc, frag in candidates:
        if frag.hidden and not include_hidden:
            continue
        s, matched = score_fragment(tokens, doc, frag, query)
        if s <= 0:
            continue
        frag_kind: DocKind = "form_reference" if PLACEHOLDER_MARK in frag.text else doc.doc_kind
        if kind is not None and frag_kind != kind:
            continue
        scored.append(
            Hit(
                source_id=doc.source_id,
                path=doc.path,
                locator=frag.locator,
                text=frag.text,
                score=s,
                hidden=frag.hidden,
                kind=frag_kind,
                revision=doc.revision,
                approval_state=doc.approval_state,
                fragment_kind=frag.fragment_kind,
                sha256=doc.sha256,
                formula=frag.formula,
                cached_value=frag.cached_value,
                unit=frag.unit,
                base_date=frag.base_date,
                synthetic=doc.synthetic,
                matched_terms=matched,
            )
        )
    # 동일 문서·동일 텍스트 중복(셀 vs 표 셀 vs named range) 은 우선순위 높은 locator 하나만 남긴다.
    best: dict[tuple[str, str, bool], Hit] = {}
    for h in scored:
        k = (h.source_id, h.text, h.hidden)
        cur = best.get(k)
        if cur is None or (_priority(h.locator), h.locator) < (_priority(cur.locator), cur.locator):
            best[k] = h
    hits = sorted(best.values(), key=lambda h: (-h.score, h.hidden, h.path, h.locator))
    return hits[: max(0, limit)]
