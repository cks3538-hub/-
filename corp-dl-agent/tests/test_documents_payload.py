"""documents.payload: report_payload schema, 합계/차이 재검증(CONFLICT), MISSING, provenance."""

from __future__ import annotations

from pathlib import Path

import pytest

from corp_dl_agent.common import Quantity
from corp_dl_agent.documents.payload import (
    PayloadCheck,
    PayloadItem,
    ReportPayload,
    format_number,
    format_quantity,
    load_payload,
    parse_number,
    payload_from_dict,
    validate_payload,
    write_payload,
)
from corp_dl_agent.documents.synthetic import example_payload
from corp_dl_agent.errors import AgentError

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "documents"


def _q(v: float | None, unit: str = "원", **kw: object) -> Quantity:
    if v is None:
        return Quantity.missing(unit)
    return Quantity(value=v, unit=unit, value_type="calculated", calculation_id="calc:test", **kw)  # type: ignore[arg-type]


def _payload(items: dict[str, Quantity], checks: list[PayloadCheck]) -> ReportPayload:
    return ReportPayload(
        report_id="R1",
        created_at="2026-09-17T00:00:00+00:00",
        subject="t",
        items={k: PayloadItem(key=k, label_ko=k, quantity=q) for k, q in items.items()},
        checks=checks,
        synthetic=True,
    )


def test_example_payload_validates_and_roundtrips(tmp_path: Path) -> None:
    payload = load_payload(FIXTURES / "report_payload.example.json")
    pv = validate_payload(payload)
    assert pv.passed and pv.provenance_issues == [] and pv.n_conflict == 0 and pv.n_missing == 1
    assert {c.check_id for c in pv.checks} == {
        "cost_total_sum",
        "cost_total_table_sum",
        "mass_delta_diff",
        "cost_delta_diff",
        "insertion_force_delta_diff",
    }
    assert all(c.status.value == "PASS" for c in pv.checks)
    assert payload.stale_values == ["X-OLD", "2023-05-01", "1,234"] and payload.synthetic
    p = write_payload(pv.payload, tmp_path / "보고" / "report_payload.json")
    assert load_payload(p) == pv.payload
    assert example_payload() == payload


def test_sum_mismatch_marks_conflict() -> None:
    p = _payload(
        {"a": _q(100), "b": _q(200), "total": _q(999)},
        [PayloadCheck(check_id="sum1", kind="sum", target="total", terms=["a", "b"])],
    )
    pv = validate_payload(p)
    assert not pv.passed and pv.n_conflict == 1
    assert pv.payload.items["total"].quantity.value_type == "conflict"
    assert pv.payload.items["total"].quantity.value is None
    assert "300" in (pv.payload.items["total"].quantity.notes or "")
    assert pv.checks[0].status.value == "FAIL" and pv.checks[0].expected == 300 and pv.checks[0].actual == 999
    # 원본 payload 는 변경되지 않는다
    assert p.items["total"].quantity.value == 999
    untouched = validate_payload(p, mark_conflicts=False)
    assert untouched.payload.items["total"].quantity.value == 999 and not untouched.passed


def test_difference_and_tolerance() -> None:
    p = _payload(
        {"a": _q(11.2, "kg"), "b": _q(10.4, "kg"), "d": _q(-0.8, "kg")},
        [PayloadCheck(check_id="diff", kind="difference", target="d", terms=["b", "a"])],
    )
    assert validate_payload(p).passed
    p2 = _payload(
        {"a": _q(11.2, "kg"), "b": _q(10.4, "kg"), "d": _q(-0.9, "kg")},
        [PayloadCheck(check_id="diff", kind="difference", target="d", terms=["b", "a"], tolerance_abs=0.05)],
    )
    assert not validate_payload(p2).passed


def test_missing_operand_with_given_total_is_conflict_and_missing_total_stays_missing() -> None:
    p = _payload(
        {"a": _q(100), "b": _q(None), "total": _q(300)},
        [PayloadCheck(check_id="s", kind="sum", target="total", terms=["a", "b"])],
    )
    pv = validate_payload(p)
    assert pv.payload.items["total"].quantity.value_type == "conflict" and not pv.passed
    p2 = _payload(
        {"a": _q(100), "b": _q(None), "total": _q(None)},
        [PayloadCheck(check_id="s", kind="sum", target="total", terms=["a", "b"])],
    )
    pv2 = validate_payload(p2)
    assert pv2.passed and pv2.checks[0].status.value == "NOT_RUN" and pv2.n_missing == 2


def test_missing_total_filled_by_engine_with_calculation_id() -> None:
    p = _payload(
        {"a": _q(100), "b": _q(200), "total": _q(None)},
        [PayloadCheck(check_id="sum1", kind="sum", target="total", terms=["a", "b"])],
    )
    pv = validate_payload(p)
    q = pv.payload.items["total"].quantity
    assert (
        pv.passed
        and q.value == 300
        and q.value_type == "calculated"
        and q.calculation_id == "payload-check:sum1"
    )
    assert q.unit == "원"


def test_unit_mismatch_is_conflict() -> None:
    p = _payload(
        {"a": _q(1, "kg"), "b": _q(2, "g"), "total": _q(3, "kg")},
        [PayloadCheck(check_id="u", kind="sum", target="total", terms=["a", "b"])],
    )
    pv = validate_payload(p)
    assert not pv.passed and pv.payload.items["total"].quantity.value_type == "conflict"
    p2 = _payload(
        {"a": _q(1, "kg"), "b": _q(2, "kg"), "total": _q(3, "g")},
        [PayloadCheck(check_id="u", kind="sum", target="total", terms=["a", "b"])],
    )
    assert "단위" in validate_payload(p2).checks[0].message_ko


def test_provenance_issues() -> None:
    p = _payload({"a": Quantity(value=1.0, unit="kg", value_type="source")}, [])
    pv = validate_payload(p)
    assert not pv.passed and any("source_locator" in i for i in pv.provenance_issues)
    p = _payload({"a": Quantity(value=1.0, unit="kg", value_type="predicted")}, [])
    assert any("model_run_id" in i for i in validate_payload(p).provenance_issues)
    p = _payload({"a": Quantity(value=None, unit="kg", value_type="calculated", calculation_id="c")}, [])
    assert any("missing" in i for i in validate_payload(p).provenance_issues)


def test_schema_rejections() -> None:
    base = example_payload().model_dump(mode="json")
    bad = dict(base)
    bad["items"] = dict(base["items"])
    bad["items"]["mass_a"] = dict(base["items"]["mass_a"])
    bad["items"]["mass_a"]["quantity"] = dict(base["items"]["mass_a"]["quantity"], value="11.2")
    with pytest.raises(AgentError) as ei:
        payload_from_dict(bad)
    assert ei.value.code == "E_SCHEMA_INVALID"
    bad2 = dict(base, extra_field=1)
    with pytest.raises(AgentError):
        payload_from_dict(bad2)
    bad3 = dict(base, checks=[{"check_id": "x", "kind": "sum", "target": "없는키", "terms": ["mass_a"]}])
    with pytest.raises(AgentError) as ei:
        payload_from_dict(bad3)
    assert "없는키" in str(ei.value.details)
    with pytest.raises(AgentError) as ei:
        load_payload(FIXTURES / "없는파일.json")
    assert ei.value.code == "E_INPUT_INVALID"
    with pytest.raises(ValueError):
        PayloadCheck(check_id="d", kind="difference", target="a", terms=["a"])


def test_format_and_parse() -> None:
    assert format_number(1980) == "1,980" and format_number(-0.8) == "-0.8" and format_number(2.5e-5) == "0"
    assert format_number(12345.6789) == "12,345.6789" and format_number(0.1 + 0.2) == "0.3"
    assert format_quantity(_q(1980)) == "1,980 원" and format_quantity(_q(1980), with_unit=False) == "1,980"
    assert format_quantity(Quantity.missing("kg")) == "MISSING"
    assert format_quantity(Quantity.conflict("kg", "x")) == "CONFLICT"
    assert parse_number("원가 1,980 원") == 1980.0 and parse_number("-0.8 kg") == -0.8
    assert parse_number("숫자 없음") is None


def test_conflict_is_never_overwritten_by_a_later_computable_check() -> None:
    """같은 대상에 두 검사가 있을 때: 첫 검사가 CONFLICT 로 판정하면 두 번째 검사가 계산값으로 덮어쓰지 않는다."""
    p = _payload(
        {"a": _q(100), "b": _q(200), "total": _q(999)},
        [
            PayloadCheck(check_id="sum_terms", kind="sum", target="total", terms=["a", "b"]),
            PayloadCheck(check_id="sum_again", kind="sum", target="total", terms=["a", "b"]),
        ],
    )
    pv = validate_payload(p)
    assert (
        not pv.passed and pv.n_conflict == 1 and pv.payload.items["total"].quantity.value_type == "conflict"
    )
    by_id = {c.check_id: c for c in pv.checks}
    assert by_id["sum_terms"].status.value == "FAIL"
    assert by_id["sum_again"].status.value == "NOT_RUN" and by_id["sum_again"].expected == 300
    assert "300" in by_id["sum_again"].message_ko
    # 입력에서 선언된 CONFLICT 도 계산값으로 대체하지 않는다
    p2 = _payload(
        {"a": _q(100), "b": _q(200), "total": Quantity.conflict("원", "견적 vs 계산 상충")},
        [PayloadCheck(check_id="s", kind="sum", target="total", terms=["a", "b"])],
    )
    pv2 = validate_payload(p2)
    assert not pv2.passed and pv2.payload.items["total"].quantity.value is None
    assert (
        pv2.payload.items["total"].quantity.notes == "견적 vs 계산 상충"
        and pv2.checks[0].status.value == "NOT_RUN"
    )
    # 피연산자 누락 + 대상 CONFLICT → 값을 만들지 않고 NOT_RUN
    p3 = _payload(
        {"a": _q(100), "b": _q(None), "total": Quantity.conflict("원", "x")},
        [PayloadCheck(check_id="s", kind="sum", target="total", terms=["a", "b"])],
    )
    pv3 = validate_payload(p3)
    assert (
        pv3.checks[0].status.value == "NOT_RUN"
        and "CONFLICT" in pv3.checks[0].message_ko
        and pv3.n_conflict == 1
    )
