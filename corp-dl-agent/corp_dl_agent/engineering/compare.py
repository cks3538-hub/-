"""설계안(A/B/C) 비교: mass, cost, performance, constraint, evidence, unknown.

- 동일 revision·기준일·통화·단위 조건이 맞지 않으면 E_REVISION_MISMATCH (require_same_revision_policy=True).
  False 이면 불일치를 warnings 에 남기고 consistent=False 로 표시한다.
- 후보가 선언한 revision 이 snapshot 의 revision 과 다르면 정책과 무관하게 거부한다.
- 직접 계산값/예측값/가정값/미확인값은 Quantity.value_type 으로 구분되며 여기서 새 숫자를 만들지 않는다.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import Field, ValidationError, field_validator

from corp_dl_agent.common import Quantity, StrictModel, now_iso, read_json, validate_strict
from corp_dl_agent.engineering.cad_snapshot import load_snapshot
from corp_dl_agent.engineering.cost import CostBreakdown
from corp_dl_agent.engineering.mass import MassResult, MassScope, compute_mass
from corp_dl_agent.errors import AgentError
from corp_dl_agent.version import CALCULATION_VERSION


class DesignCandidate(StrictModel):
    id: str
    label: str
    snapshot_path: str
    revision: str
    base_date: str | None = None
    currency: str | None = None
    mass: MassResult
    cost: CostBreakdown | None = None
    performance: dict[str, Quantity] = Field(default_factory=dict)
    constraints: dict[str, bool | None] = Field(default_factory=dict)
    evidence: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    synthetic: bool = False

    @field_validator("id", "revision")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("빈 값은 허용되지 않습니다")
        return v

    @field_validator("base_date")
    @classmethod
    def _date(cls, v: str | None) -> str | None:
        if v is None:
            return None
        try:
            date.fromisoformat(v.strip())
        except ValueError as exc:
            raise ValueError("기준일은 YYYY-MM-DD 형식이어야 합니다") from exc
        return v.strip()

    @field_validator("currency")
    @classmethod
    def _cur(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip().upper()
        if len(v) != 3 or not v.isalpha():
            raise ValueError("통화는 3글자 코드여야 합니다")
        return v


class ComparisonRow(StrictModel):
    candidate_id: str
    label: str
    revision: str
    base_date: str | None
    currency: str | None
    mass_total: Quantity
    mass_complete: bool
    mass_missing: list[str]
    unit_cost: Quantity
    cost_complete: bool | None  # None: cost 없음
    cost_missing: list[str]
    estimate_type: str | None
    performance: dict[str, Quantity]
    constraints: dict[str, bool | None]
    constraints_ok: bool | None  # None: 미확인 constraint 있음
    evidence: list[str]
    unknowns: list[str]
    synthetic: bool


class DeltaRow(StrictModel):
    metric: str  # "mass_total" | "unit_cost" | "performance:<key>"
    candidate_id: str
    baseline_id: str
    delta: Quantity  # candidate - baseline (MISSING 이면 value=None)
    relative: float | None = None  # delta / baseline


class DesignComparison(StrictModel):
    created_at: str
    calculation_version: str
    baseline_id: str
    revision: str | None  # 공통 revision (불일치 허용 시 None)
    base_date: str | None
    currency: str | None
    rows: list[ComparisonRow]
    deltas: list[DeltaRow]
    warnings: list[str]
    consistent: bool
    complete: bool  # 모든 후보의 mass/cost 가 complete
    require_same_revision_policy: bool
    synthetic: bool


def _all_equal(values: list[str | None]) -> bool:
    present = [v for v in values if v is not None]
    return len(set(present)) <= 1


def _delta(metric: str, cand: DesignCandidate, base: DesignCandidate, a: Quantity, b: Quantity) -> DeltaRow:
    calc_id = f"delta:{metric}:{cand.id}-{base.id}"
    if a.is_missing() or b.is_missing() or a.value is None or b.value is None:
        why = []
        if a.is_missing():
            why.append(f"{cand.id} {metric} MISSING")
        if b.is_missing():
            why.append(f"{base.id} {metric} MISSING")
        return DeltaRow(
            metric=metric,
            candidate_id=cand.id,
            baseline_id=base.id,
            delta=Quantity.missing(a.unit or b.unit, "; ".join(why)),
        )
    if a.unit != b.unit:
        raise AgentError(
            "E_REVISION_MISMATCH", f"{metric} 단위 불일치: {cand.id} '{a.unit}' ≠ {base.id} '{b.unit}'"
        )
    d = a.value - b.value
    rel = d / b.value if b.value not in (0, 0.0) else None
    assumed = a.assumption or b.assumption or a.value_type == "predicted" or b.value_type == "predicted"
    return DeltaRow(
        metric=metric,
        candidate_id=cand.id,
        baseline_id=base.id,
        delta=Quantity(
            value=d,
            unit=a.unit,
            value_type="calculated",
            calculation_id=calc_id,
            calculation_version=CALCULATION_VERSION,
            assumption=assumed,
            notes=f"{cand.id}({a.value_type}) - {base.id}({b.value_type})",
        ),
        relative=rel,
    )


def compare_designs(
    candidates: list[DesignCandidate],
    *,
    require_same_revision_policy: bool = True,
    baseline_id: str | None = None,
) -> DesignComparison:
    if len(candidates) < 2:
        raise AgentError(
            "E_INPUT_INVALID", "비교하려면 후보가 2개 이상 필요합니다", details={"n": len(candidates)}
        )
    ids = [c.id for c in candidates]
    if len(set(ids)) != len(ids):
        raise AgentError("E_INPUT_INVALID", "후보 id 가 중복됩니다", details={"ids": ids})
    base = (
        candidates[0] if baseline_id is None else next((c for c in candidates if c.id == baseline_id), None)
    )
    if base is None:
        raise AgentError(
            "E_INPUT_INVALID", f"기준 후보를 찾을 수 없습니다: {baseline_id}", details={"ids": ids}
        )

    warnings: list[str] = []
    mismatches: list[str] = []

    def mismatch(msg: str, details: dict[str, Any]) -> None:
        if require_same_revision_policy:
            raise AgentError("E_REVISION_MISMATCH", msg, details=details)
        mismatches.append(msg)

    # 후보 선언 revision vs snapshot revision (정책과 무관하게 거부)
    for c in candidates:
        if c.mass.revisions and c.revision not in c.mass.revisions:
            raise AgentError(
                "E_REVISION_MISMATCH",
                f"후보 {c.id} 의 revision '{c.revision}' 이 snapshot revision {c.mass.revisions} 과 다릅니다",
                details={"candidate": c.id, "declared": c.revision, "snapshot": c.mass.revisions},
            )
        if c.cost is not None:
            if c.currency is None or c.base_date is None:
                raise AgentError(
                    "E_INPUT_INVALID",
                    f"후보 {c.id} 에 cost 가 있으나 currency/base_date 가 선언되지 않았습니다",
                    details={"candidate": c.id, "currency": c.currency, "base_date": c.base_date},
                )
            if c.cost.currency != c.currency:
                raise AgentError(
                    "E_REVISION_MISMATCH",
                    f"후보 {c.id} 의 통화 '{c.currency}' 와 cost_breakdown 통화 '{c.cost.currency}' 가 다릅니다",
                    details={"candidate": c.id},
                )
            if c.cost.base_date != c.base_date:
                raise AgentError(
                    "E_REVISION_MISMATCH",
                    f"후보 {c.id} 의 기준일 '{c.base_date}' 와 cost_breakdown 기준일 '{c.cost.base_date}' 가 다릅니다",
                    details={"candidate": c.id},
                )
        if c.mass.total.unit != "kg":
            raise AgentError(
                "E_UNIT_MISMATCH", f"후보 {c.id} 의 질량 단위가 kg 이 아닙니다: '{c.mass.total.unit}'"
            )

    revisions = [c.revision for c in candidates]
    if len(set(revisions)) > 1:
        mismatch(
            "후보 간 revision 불일치: " + ", ".join(f"{c.id}={c.revision}" for c in candidates),
            {"revisions": revisions},
        )
    dates = [c.base_date for c in candidates]
    if not _all_equal(dates):
        mismatch(
            "후보 간 기준일 불일치: " + ", ".join(f"{c.id}={c.base_date}" for c in candidates),
            {"base_dates": dates},
        )
    currencies = [c.currency for c in candidates]
    if not _all_equal(currencies):
        mismatch(
            "후보 간 통화 불일치: " + ", ".join(f"{c.id}={c.currency}" for c in candidates),
            {"currencies": currencies},
        )
    calc_versions = {c.mass.calculation_version for c in candidates}
    if len(calc_versions) > 1:
        warnings.append(f"후보 간 계산식 버전이 다릅니다: {sorted(calc_versions)}")
    if any(c.base_date is None for c in candidates) and any(c.base_date is not None for c in candidates):
        warnings.append("일부 후보에 기준일이 없습니다 (cost 없는 후보)")
    # performance 단위 정합
    perf_units: dict[str, set[str]] = {}
    for c in candidates:
        for k, q in c.performance.items():
            perf_units.setdefault(k, set()).add(q.unit)
    for k, us in perf_units.items():
        if len(us) > 1:
            mismatch(f"performance '{k}' 단위 불일치: {sorted(us)}", {"key": k, "units": sorted(us)})

    rows: list[ComparisonRow] = []
    for c in candidates:
        unknowns = list(c.unknowns)
        for m in c.mass.missing:
            unknowns.append(f"mass: {m}")
        if c.cost is None:
            unknowns.append("cost: cost_breakdown 없음")
        else:
            for m in c.cost.missing:
                unknowns.append(f"cost: {m}")
        for k in perf_units:
            if k not in c.performance:
                unknowns.append(f"performance: {k} 없음")
            elif c.performance[k].is_missing():
                unknowns.append(f"performance: {k} MISSING")
        for k, v in c.constraints.items():
            if v is None:
                unknowns.append(f"constraint: {k} 미확인")
        cons_vals = list(c.constraints.values())
        constraints_ok: bool | None = None if any(v is None for v in cons_vals) else all(cons_vals)
        rows.append(
            ComparisonRow(
                candidate_id=c.id,
                label=c.label,
                revision=c.revision,
                base_date=c.base_date,
                currency=c.currency,
                mass_total=c.mass.total,
                mass_complete=c.mass.complete,
                mass_missing=list(c.mass.missing),
                unit_cost=c.cost.unit_cost
                if c.cost is not None
                else Quantity.missing("", "cost_breakdown 없음"),
                cost_complete=c.cost.complete if c.cost is not None else None,
                cost_missing=list(c.cost.missing) if c.cost is not None else [],
                estimate_type=c.cost.estimate_type if c.cost is not None else None,
                performance=dict(c.performance),
                constraints=dict(c.constraints),
                constraints_ok=constraints_ok,
                evidence=list(c.evidence),
                unknowns=unknowns,
                synthetic=c.synthetic or c.mass.synthetic,
            )
        )

    deltas: list[DeltaRow] = []
    for c in candidates:
        if c.id == base.id:
            continue
        deltas.append(_delta("mass_total", c, base, c.mass.total, base.mass.total))
        cq = c.cost.unit_cost if c.cost is not None else Quantity.missing("", "cost 없음")
        bq = base.cost.unit_cost if base.cost is not None else Quantity.missing("", "cost 없음")
        deltas.append(_delta("unit_cost", c, base, cq, bq))
        for k in sorted(perf_units):
            a = c.performance.get(k, Quantity.missing("", "없음"))
            b = base.performance.get(k, Quantity.missing("", "없음"))
            deltas.append(_delta(f"performance:{k}", c, base, a, b))

    warnings.extend(mismatches)
    consistent = not mismatches
    complete = all(c.mass.complete and (c.cost is not None and c.cost.complete) for c in candidates)
    return DesignComparison(
        created_at=now_iso(),
        calculation_version=CALCULATION_VERSION,
        baseline_id=base.id,
        revision=revisions[0] if len(set(revisions)) == 1 else None,
        base_date=dates[0] if _all_equal(dates) and dates[0] is not None else None,
        currency=currencies[0] if _all_equal(currencies) and currencies[0] is not None else None,
        rows=rows,
        deltas=deltas,
        warnings=warnings,
        consistent=consistent,
        complete=complete,
        require_same_revision_policy=require_same_revision_policy,
        synthetic=any(r.synthetic for r in rows),
    )


# ---------------------------------------------------------------------------
# 후보 폴더 로드 (CLI 용)
# ---------------------------------------------------------------------------

CANDIDATE_FILES = {
    "snapshot": "cad_snapshot.json",
    "mass": "mass_result.json",
    "cost": "cost_breakdown.json",
    "meta": "candidate.json",
}


def _load_model(path: Path, model: type[Any], label: str) -> Any:
    try:
        return validate_strict(model, read_json(path))
    except (ValidationError, json.JSONDecodeError) as exc:
        raise AgentError(
            "E_SCHEMA_INVALID",
            f"{label} 파일이 schema 와 일치하지 않습니다: {path.name}",
            details={"path": str(path), "error": str(exc)[:500]},
        ) from exc


def load_candidate_dir(
    directory: str | Path,
    *,
    candidate_id: str,
    label: str | None = None,
    scope: MassScope | None = None,
) -> DesignCandidate:
    """후보 폴더: cad_snapshot.json (필수, mass_result.json 없으면 계산), cost_breakdown.json / candidate.json (선택)."""
    d = Path(directory)
    if not d.is_dir():
        raise AgentError(
            "E_INPUT_INVALID",
            f"후보 폴더가 없습니다: {d}",
            details={"candidate": candidate_id, "path": str(d)},
        )
    meta: dict[str, Any] = {}
    meta_path = d / CANDIDATE_FILES["meta"]
    if meta_path.is_file():
        loaded = read_json(meta_path)
        if not isinstance(loaded, dict):
            raise AgentError("E_SCHEMA_INVALID", f"candidate.json 은 객체여야 합니다: {meta_path}")
        meta = loaded
    mass_path = d / CANDIDATE_FILES["mass"]
    snap_path = d / CANDIDATE_FILES["snapshot"]
    if mass_path.is_file():
        mass: MassResult = _load_model(mass_path, MassResult, "mass_result")
        snapshot_path = mass.source_path
    elif snap_path.is_file():
        snap = load_snapshot(snap_path)
        mass = compute_mass(snap, scope=scope)
        snapshot_path = str(snap_path)
    else:
        raise AgentError(
            "E_INPUT_INVALID",
            f"후보 {candidate_id}: cad_snapshot.json 또는 mass_result.json 이 필요합니다",
            details={"path": str(d)},
        )
    cost: CostBreakdown | None = None
    cost_path = d / CANDIDATE_FILES["cost"]
    if cost_path.is_file():
        cost = _load_model(cost_path, CostBreakdown, "cost_breakdown")
    revision = meta.get("revision")
    if revision is None:
        if len(mass.revisions) == 1:
            revision = mass.revisions[0]
        else:
            raise AgentError(
                "E_INPUT_INVALID",
                f"후보 {candidate_id}: snapshot revision 이 단일하지 않아 candidate.json 에 revision 을 명시해야 합니다",
                details={"revisions": mass.revisions},
            )
    performance: dict[str, Quantity] = {}
    for k, v in (meta.get("performance") or {}).items():
        try:
            performance[str(k)] = validate_strict(Quantity, v)
        except ValidationError as exc:
            raise AgentError(
                "E_SCHEMA_INVALID",
                f"performance '{k}' 는 Quantity 형식이어야 합니다",
                details={"error": str(exc)[:300]},
            ) from exc
    data = {
        "id": candidate_id,
        "label": meta.get("label") or label or candidate_id,
        "snapshot_path": snapshot_path,
        "revision": revision,
        "base_date": meta.get("base_date", cost.base_date if cost else None),
        "currency": meta.get("currency", cost.currency if cost else None),
        "mass": mass.model_dump(mode="json"),
        "cost": cost.model_dump(mode="json") if cost else None,
        "performance": {k: q.model_dump(mode="json") for k, q in performance.items()},
        "constraints": meta.get("constraints") or {},
        "evidence": meta.get("evidence") or [],
        "unknowns": meta.get("unknowns") or [],
        "synthetic": bool(meta.get("synthetic", mass.synthetic)),
    }
    try:
        return validate_strict(DesignCandidate, data)
    except ValidationError as exc:
        raise AgentError(
            "E_INPUT_INVALID",
            f"후보 {candidate_id} 정의가 올바르지 않습니다",
            details={
                "errors": [f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors()[:20]]
            },
        ) from exc


def comparison_summary(cmp: DesignComparison) -> dict[str, Any]:
    return {
        "baseline": cmp.baseline_id,
        "revision": cmp.revision,
        "base_date": cmp.base_date,
        "currency": cmp.currency,
        "consistent": cmp.consistent,
        "complete": cmp.complete,
        "rows": [
            {
                "id": r.candidate_id,
                "label": r.label,
                "mass_kg": r.mass_total.value,
                "mass_complete": r.mass_complete,
                "unit_cost": r.unit_cost.value,
                "cost_complete": r.cost_complete,
                "estimate_type": r.estimate_type,
                "constraints_ok": r.constraints_ok,
                "unknowns": len(r.unknowns),
            }
            for r in cmp.rows
        ],
        "deltas": [
            {"metric": d.metric, "candidate": d.candidate_id, "delta": d.delta.value, "unit": d.delta.unit}
            for d in cmp.deltas
        ],
        "warnings": cmp.warnings,
        "synthetic": cmp.synthetic,
    }
