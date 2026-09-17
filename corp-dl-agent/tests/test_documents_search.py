"""documents.search / evidence: 키워드 검색, hidden 제외, form_reference vs fact, evidence.json."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from corp_dl_agent.documents.evidence import build_evidence, load_evidence, write_evidence
from corp_dl_agent.documents.index import DocumentIndex
from corp_dl_agent.documents.search import search, tokenize

pytestmark = pytest.mark.documents

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "documents"


@pytest.fixture
def index(tmp_path: Path) -> Iterator[DocumentIndex]:
    d = tmp_path / "근거 자료"
    shutil.copytree(FIXTURES, d)
    idx = DocumentIndex(tmp_path / "documents.sqlite")
    idx.index_roots([d])
    yield idx
    idx.close()


def test_tokenize_phrases() -> None:
    assert tokenize('X-OLD "단가 기준" 1,234') == ["x-old", "단가 기준", "1,234"]
    assert tokenize("   ") == []


def test_search_finds_slide_cell_and_named_range(index: DocumentIndex) -> None:
    hits = search(index, "1,234 원", limit=20)
    locs = {(Path(h.path).name, h.locator) for h in hits}
    assert ("past_review_2023.pptx", "slide:2/shape:Body 1") in locs
    assert ("past_review_2023.pptx", "slide:2/notes") in locs
    assert ("past_cost_table.xlsx", "sheet:Cost!B11") in locs
    assert ("past_cost_table.xlsx", "range:UnitCost") in locs  # cached 1234 (콤마 정규화)
    top = hits[0]
    assert top.score >= 2.0 and top.kind == "fact" and top.hidden is False and top.synthetic is True
    unit_cost = next(h for h in hits if h.locator == "range:UnitCost")
    assert unit_cost.formula == "=SUM(B2:B6)" and unit_cost.cached_value == "1234"
    assert all(not h.hidden for h in hits)
    # 결정적 정렬
    assert [h.locator for h in search(index, "1,234 원", limit=20)] == [h.locator for h in hits]


def test_hidden_excluded_unless_explicit(index: DocumentIndex) -> None:
    assert search(index, "1,100") == []
    hits = search(index, "1,100", include_hidden=True)
    assert {h.locator for h in hits} == {"slide:4/shape:Memo 1", "sheet:내부메모!A1"}
    assert all(h.hidden for h in hits)


def test_kind_filter_separates_form_reference_from_fact(index: DocumentIndex) -> None:
    facts = search(index, "원가", kind="fact")
    forms = search(index, "원가", kind="form_reference")
    assert facts and all(h.kind == "fact" for h in facts)
    assert forms and all(h.kind == "form_reference" for h in forms)
    assert all("template" in Path(h.path).name for h in forms)
    assert any("{{" in h.text for h in search(index, "cost_a", kind="form_reference"))


def test_metadata_match_and_dedupe(index: DocumentIndex) -> None:
    hits = search(index, "X-OLD 단가")
    assert hits[0].locator == "sheet:Cost!B11"
    # 표 셀과 시트 셀의 중복 텍스트는 하나만 남는다 (같은 문서·같은 텍스트)
    keys = [(h.source_id, h.text) for h in hits]
    assert len(keys) == len(set(keys))
    assert any(m.startswith("meta:") for h in hits for m in h.matched_terms)
    assert search(index, "존재하지않는검색어zzz") == []
    assert len(search(index, "원", limit=3)) == 3


def test_build_evidence_excludes_hidden_and_roundtrips(index: DocumentIndex, tmp_path: Path) -> None:
    hits = search(index, "1,100 단가", include_hidden=True)
    ev = build_evidence(hits, purpose="시험", query="1,100 단가")
    assert ev.excluded_hidden == 2 and all(not i.hidden for i in ev.items)
    assert ev.n_items == len(ev.items) == ev.n_fact + ev.n_form_reference
    assert ev.synthetic is True and any("숨김" in n for n in ev.notes)
    item = ev.items[0]
    assert item.sha256 and item.locator and item.quoted_text and item.evidence_id.startswith("ev-")
    allowed = build_evidence(hits, purpose="시험", allow_hidden=True)
    assert allowed.excluded_hidden == 0 and any(i.hidden for i in allowed.items)
    p = write_evidence(ev, tmp_path / "근거" / "evidence.json")
    loaded = load_evidence(p)
    assert loaded == ev
    empty = build_evidence([], purpose="없음")
    assert empty.n_items == 0 and empty.synthetic is False and empty.notes


def test_evidence_truncates_long_quotes(index: DocumentIndex) -> None:
    hits = search(index, "설계안")
    ev = build_evidence(hits, purpose="짧게", max_quote_chars=5)
    assert all(i.truncated and len(i.quoted_text) == 6 for i in ev.items)
