"""report_payload.json: PPTX/XLSX 수치의 단일 원본.

- 각 항목은 Quantity(value/unit/value_type/source_locator|calculation_id|model_run_id) 를 가진다.
- 없는 값은 MISSING(value_type=missing), 출처 간 상충은 CONFLICT(value_type=conflict).
- checks 로 선언된 합계(sum)/차이(difference) 는 validate_payload 가 계산 엔진으로 재검증한다.
  불일치는 대상 항목을 CONFLICT 로 바꾸고(notes 에 기대값·실제값 기록), 결과에 CheckResult 로 남긴다.
- LLM/코드가 없는 숫자를 만들어 채우지 않는다. 계산 엔진이 채울 수 있는 값(피연산자가 모두 있는 합계/차이)만
  calculated 로 채우며 calculation_id 에 check_id 를 남긴다.
"""

from __future__ import annotations

import math
import os
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from corp_dl_agent.common import (
    Quantity,
    Status,
    StrictModel,
    atomic_write_json,
    now_iso,
    read_json,
    validate_strict,
)
from corp_dl_agent.errors import AgentError
from corp_dl_agent.version import CALCULATION_VERSION, SCHEMA_VERSION

MISSING_TEXT = "MISSING"
CONFLICT_TEXT = "CONFLICT"
PAYLOAD_CALC_VERSION = f"payload-check-{SCHEMA_VERSION}"

_NUMBER_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?(?:[eE][-+]?\d+)?")
_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class PayloadItem(StrictModel):
    key: str
    label_ko: str
    quantity: Quantity


class PayloadText(StrictModel):
    """수치가 아닌 문자열 항목(차종명·결론 등). origin 으로 출처를 구분한다."""

    key: str
    label_ko: str
    text: str
    origin: Literal["user", "source", "calculated", "template"] = "user"
    source_locator: str | None = None


class PayloadColumn(StrictModel):
    key: str
    label_ko: str
    unit: str = ""


class PayloadRow(StrictModel):
    label_ko: str
    cells: dict[str, Quantity] = Field(default_factory=dict)


class PayloadTable(StrictModel):
    name: str
    label_ko: str
    columns: list[PayloadColumn]
    rows: list[PayloadRow] = Field(default_factory=list)


class PayloadCheck(StrictModel):
    """계산 엔진 재검증 규칙.

    - kind="sum":        target == sum(terms)  또는 target == sum(tables[table].rows[*].cells[column])
    - kind="difference": target == terms[0] - terms[1]
    """

    check_id: str
    kind: Literal["sum", "difference"]
    target: str
    terms: list[str] = Field(default_factory=list)
    table: str | None = None
    column: str | None = None
    tolerance_abs: float = 1e-6
    description_ko: str = ""

    @model_validator(mode="after")
    def _shape(self) -> PayloadCheck:
        if self.kind == "difference" and len(self.terms) != 2:
            raise ValueError(f"difference check '{self.check_id}' 는 terms 2개(피감수, 감수)가 필요합니다")
        if self.kind == "sum" and not self.terms and not (self.table and self.column):
            raise ValueError(f"sum check '{self.check_id}' 는 terms 또는 table+column 이 필요합니다")
        if self.tolerance_abs < 0:
            raise ValueError("tolerance_abs 는 0 이상이어야 합니다")
        return self


class ReportPayload(StrictModel):
    schema_version: str = SCHEMA_VERSION
    report_id: str
    created_at: str
    subject: str
    items: dict[str, PayloadItem]
    tables: dict[str, PayloadTable] = Field(default_factory=dict)
    texts: dict[str, PayloadText] = Field(default_factory=dict)
    checks: list[PayloadCheck] = Field(default_factory=list)
    stale_values: list[str] = Field(default_factory=list)
    synthetic: bool
    data_origin: Literal["synthetic", "corporate"] = "synthetic"
    calculation_version: str = CALCULATION_VERSION
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _keys_consistent(self) -> ReportPayload:
        for k, it in self.items.items():
            if k != it.key:
                raise ValueError(f"items['{k}'].key 가 '{it.key}' 로 다릅니다")
            if not _KEY_RE.match(k):
                raise ValueError(f"item key '{k}' 는 영문/숫자/밑줄만 허용합니다")
        for k, t in self.texts.items():
            if k != t.key:
                raise ValueError(f"texts['{k}'].key 가 '{t.key}' 로 다릅니다")
            if k in self.items:
                raise ValueError(f"key '{k}' 가 items 와 texts 에 모두 있습니다")
            if not _KEY_RE.match(k):
                raise ValueError(f"text key '{k}' 는 영문/숫자/밑줄만 허용합니다")
        for k, tb in self.tables.items():
            if k != tb.name:
                raise ValueError(f"tables['{k}'].name 이 '{tb.name}' 로 다릅니다")
            cols = {c.key for c in tb.columns}
            for r in tb.rows:
                unknown = set(r.cells) - cols
                if unknown:
                    raise ValueError(f"table '{k}' 행 '{r.label_ko}' 에 정의되지 않은 열 {sorted(unknown)}")
        seen: set[str] = set()
        for c in self.checks:
            if c.check_id in seen:
                raise ValueError(f"check_id 중복: {c.check_id}")
            seen.add(c.check_id)
            if c.target not in self.items:
                raise ValueError(f"check '{c.check_id}' 의 target '{c.target}' 가 items 에 없습니다")
            for t in c.terms:
                if t not in self.items:
                    raise ValueError(f"check '{c.check_id}' 의 term '{t}' 가 items 에 없습니다")
            if c.table is not None:
                if c.table not in self.tables:
                    raise ValueError(f"check '{c.check_id}' 의 table '{c.table}' 가 tables 에 없습니다")
                if c.column is None or c.column not in {col.key for col in self.tables[c.table].columns}:
                    raise ValueError(f"check '{c.check_id}' 의 column '{c.column}' 가 table 에 없습니다")
        if self.data_origin == "synthetic" and not self.synthetic:
            raise ValueError("data_origin=synthetic 이면 synthetic=true 여야 합니다")
        return self


class CheckResult(StrictModel):
    check_id: str
    kind: str
    target: str
    status: Status
    expected: float | None = None
    actual: float | None = None
    unit: str = ""
    message_ko: str = ""


class PayloadValidation(StrictModel):
    passed: bool
    checks: list[CheckResult] = Field(default_factory=list)
    provenance_issues: list[str] = Field(default_factory=list)
    n_items: int = 0
    n_missing: int = 0
    n_conflict: int = 0
    calculation_version: str = PAYLOAD_CALC_VERSION
    payload: ReportPayload


# ---------------------------------------------------------------------------
# 형식화 / 파싱
# ---------------------------------------------------------------------------


def format_number(value: float) -> str:
    """결정적 숫자 표기: 정수는 천단위 콤마, 실수는 최대 4자리(후행 0 제거)."""
    if math.isnan(value) or math.isinf(value):
        return CONFLICT_TEXT
    if float(value).is_integer():
        return f"{int(round(value)):,}"
    s = f"{value:,.4f}".rstrip("0").rstrip(".")
    return s if s not in ("-0", "") else "0"


def format_quantity(q: Quantity, *, with_unit: bool = True) -> str:
    if q.value_type == "conflict":
        return CONFLICT_TEXT
    if q.value is None or q.value_type == "missing":
        return MISSING_TEXT
    text = format_number(q.value)
    if with_unit and q.unit:
        return f"{text} {q.unit}"
    return text


def parse_number(text: str) -> float | None:
    """문서 텍스트에서 첫 숫자를 읽는다 (천단위 콤마 허용). 없으면 None."""
    m = _NUMBER_RE.search(text or "")
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def provenance_of(q: Quantity) -> str:
    if q.value_type == "source":
        return q.source_locator or ""
    if q.value_type == "calculated":
        return q.calculation_id or ""
    if q.value_type == "predicted":
        return q.model_run_id or ""
    if q.value_type == "assumed":
        return "assumption"
    return q.value_type.upper()


# ---------------------------------------------------------------------------
# 검증
# ---------------------------------------------------------------------------


def _provenance_issues(payload: ReportPayload) -> list[str]:
    issues: list[str] = []
    all_q: list[tuple[str, Quantity]] = [(f"items.{k}", it.quantity) for k, it in payload.items.items()]
    for tname, tb in payload.tables.items():
        for i, row in enumerate(tb.rows):
            for ck, q in row.cells.items():
                all_q.append((f"tables.{tname}[{i}].{ck}", q))
    for name, q in all_q:
        vt = q.value_type
        if vt == "source" and not q.source_locator:
            issues.append(f"{name}: value_type=source 이지만 source_locator 가 없습니다")
        elif vt == "calculated" and not q.calculation_id:
            issues.append(f"{name}: value_type=calculated 이지만 calculation_id 가 없습니다")
        elif vt == "predicted" and not q.model_run_id:
            issues.append(f"{name}: value_type=predicted 이지만 model_run_id 가 없습니다")
        elif vt == "assumed" and not q.assumption:
            issues.append(f"{name}: value_type=assumed 이지만 assumption=false 입니다")
        if vt in ("missing", "conflict") and q.value is not None:
            issues.append(f"{name}: value_type={vt} 이지만 value 가 있습니다")
        if vt not in ("missing", "conflict") and q.value is None:
            issues.append(f"{name}: value 가 없는데 value_type={vt} 입니다 (missing 으로 표시해야 합니다)")
        if q.value is not None and (math.isnan(q.value) or math.isinf(q.value)):
            issues.append(f"{name}: 유한하지 않은 값")
    return issues


def _operands(payload: ReportPayload, check: PayloadCheck) -> list[tuple[str, Quantity]]:
    if check.table is not None and check.column is not None:
        tb = payload.tables[check.table]
        return [
            (f"{check.table}[{i}].{check.column}", row.cells.get(check.column, Quantity.missing()))
            for i, row in enumerate(tb.rows)
        ]
    return [(t, payload.items[t].quantity) for t in check.terms]


def validate_payload(payload: ReportPayload, *, mark_conflicts: bool = True) -> PayloadValidation:
    """합계/차이를 재계산하여 검증한다. 불일치 대상은 CONFLICT 로 표시한 payload 사본을 돌려준다."""
    work = payload.model_copy(deep=True)
    results: list[CheckResult] = []
    for check in work.checks:
        target_item = work.items[check.target]
        tq = target_item.quantity
        ops = _operands(work, check)
        missing_ops = [n for n, q in ops if q.is_missing()]
        units = {(q.unit or "").strip() for _, q in ops if not q.is_missing()}
        unit = next(iter(units)) if len(units) == 1 else ""
        if missing_ops:
            if tq.is_missing():
                results.append(
                    CheckResult(
                        check_id=check.check_id,
                        kind=check.kind,
                        target=check.target,
                        status=Status.NOT_RUN,
                        unit=unit,
                        message_ko=f"피연산자 누락({', '.join(missing_ops)}) — 대상도 MISSING 이므로 일관됩니다",
                    )
                )
            else:
                msg = f"피연산자 누락({', '.join(missing_ops)}) 상태에서 대상 값 {format_number(tq.value or 0.0)} 는 검증 불가"
                if mark_conflicts:
                    target_item.quantity = Quantity.conflict(tq.unit, f"{check.check_id}: {msg}")
                results.append(
                    CheckResult(
                        check_id=check.check_id,
                        kind=check.kind,
                        target=check.target,
                        status=Status.FAIL,
                        actual=tq.value,
                        unit=unit,
                        message_ko=msg,
                    )
                )
            continue
        if len(units) > 1:
            msg = f"피연산자 단위 불일치: {sorted(units)}"
            if mark_conflicts:
                target_item.quantity = Quantity.conflict(tq.unit, f"{check.check_id}: {msg}")
            results.append(
                CheckResult(
                    check_id=check.check_id,
                    kind=check.kind,
                    target=check.target,
                    status=Status.FAIL,
                    actual=tq.value,
                    message_ko=msg,
                )
            )
            continue
        values = [float(q.value) for _, q in ops if q.value is not None]
        expected = sum(values) if check.kind == "sum" else values[0] - values[1]
        if tq.is_missing():
            # 계산 엔진이 채울 수 있는 값: 피연산자가 모두 있으므로 calculated 로 기록
            target_item.quantity = Quantity(
                value=expected,
                unit=unit,
                value_type="calculated",
                calculation_id=f"payload-check:{check.check_id}",
                calculation_version=PAYLOAD_CALC_VERSION,
                notes=f"{check.kind} 재계산으로 채움",
            )
            results.append(
                CheckResult(
                    check_id=check.check_id,
                    kind=check.kind,
                    target=check.target,
                    status=Status.PASS,
                    expected=expected,
                    actual=expected,
                    unit=unit,
                    message_ko="대상이 MISSING 이어서 계산 엔진 결과로 채웠습니다",
                )
            )
            continue
        target_unit = (tq.unit or "").strip()
        if unit and target_unit and unit != target_unit:
            msg = f"대상 단위 '{target_unit}' 가 피연산자 단위 '{unit}' 와 다릅니다"
            if mark_conflicts:
                target_item.quantity = Quantity.conflict(tq.unit, f"{check.check_id}: {msg}")
            results.append(
                CheckResult(
                    check_id=check.check_id,
                    kind=check.kind,
                    target=check.target,
                    status=Status.FAIL,
                    expected=expected,
                    actual=tq.value,
                    unit=unit,
                    message_ko=msg,
                )
            )
            continue
        actual = float(tq.value) if tq.value is not None else math.nan
        ok = abs(actual - expected) <= check.tolerance_abs
        if ok:
            results.append(
                CheckResult(
                    check_id=check.check_id,
                    kind=check.kind,
                    target=check.target,
                    status=Status.PASS,
                    expected=expected,
                    actual=actual,
                    unit=unit,
                    message_ko="계산 엔진 재검증 일치",
                )
            )
        else:
            msg = f"불일치: payload={format_number(actual)} vs 재계산={format_number(expected)} (허용 {check.tolerance_abs})"
            if mark_conflicts:
                target_item.quantity = Quantity.conflict(tq.unit, f"{check.check_id}: {msg}")
            results.append(
                CheckResult(
                    check_id=check.check_id,
                    kind=check.kind,
                    target=check.target,
                    status=Status.FAIL,
                    expected=expected,
                    actual=actual,
                    unit=unit,
                    message_ko=msg,
                )
            )
    prov = _provenance_issues(work)
    n_missing = sum(1 for it in work.items.values() if it.quantity.value_type == "missing")
    n_conflict = sum(1 for it in work.items.values() if it.quantity.value_type == "conflict")
    passed = not prov and all(r.status in (Status.PASS, Status.NOT_RUN) for r in results) and n_conflict == 0
    return PayloadValidation(
        passed=passed,
        checks=results,
        provenance_issues=prov,
        n_items=len(work.items),
        n_missing=n_missing,
        n_conflict=n_conflict,
        payload=work,
    )


# ---------------------------------------------------------------------------
# 입출력
# ---------------------------------------------------------------------------


def load_payload(path: str | os.PathLike[str]) -> ReportPayload:
    p = Path(path)
    if not p.is_file():
        raise AgentError("E_INPUT_INVALID", f"report_payload 파일이 없습니다: {p.name}", details={"path": str(p)})
    try:
        data = read_json(p)
    except ValueError as exc:
        raise AgentError("E_INPUT_INVALID", f"report_payload JSON 파싱 실패: {p.name}", details={"error": str(exc)[:300]}) from exc
    return payload_from_dict(data)


def payload_from_dict(data: Any) -> ReportPayload:
    from pydantic import ValidationError

    try:
        payload: ReportPayload = validate_strict(ReportPayload, data)
    except ValidationError as exc:
        errs = [f"{'.'.join(str(x) for x in e.get('loc', ()))}: {e.get('msg', '')}" for e in exc.errors()]
        raise AgentError("E_SCHEMA_INVALID", "report_payload schema 검증 실패", details={"errors": errs[:20]}) from exc
    return payload


def write_payload(payload: ReportPayload, path: str | os.PathLike[str]) -> Path:
    p = Path(path)
    atomic_write_json(p, payload.model_dump(mode="json"))
    return p


def new_payload(
    *,
    report_id: str,
    subject: str,
    synthetic: bool,
    items: dict[str, PayloadItem] | None = None,
    created_at: str | None = None,
    data_origin: Literal["synthetic", "corporate"] | None = None,
) -> ReportPayload:
    return ReportPayload(
        report_id=report_id,
        created_at=created_at or now_iso(),
        subject=subject,
        items=items or {},
        synthetic=synthetic,
        data_origin=data_origin or ("synthetic" if synthetic else "corporate"),
    )
