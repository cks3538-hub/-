"""중량 계산: kg = m3 × kg/m3 (유효 솔리드 직접 계산).

규칙
- suppressed / assembly 총량 노드는 합산에서 제외한다 (상위 총량 + 하위 부품 이중합산 금지).
- 같은 reference 가 여러 occurrence 로 등장하면 각 occurrence 의 quantity(및 상위 조립체 quantity) 만큼 곱한다.
- unloaded / 밀도 누락 / surface(두께·면적 미지정) / geometry missing 은 MISSING 이며 total.complete=False.
- 밀도 기본값(재료표)은 policy 가 허용할 때만 사용하며 assumption=True 로 표시한다.
- CAD 가 직접 준 질량(mass_kg)은 출처를 구분해 보존하고 계산값과 비교한다. 허용오차 밖이면 CONFLICT.
- 도장/표피/폼/접착제/미모델링 구매품은 MassScope 로 포함 범위를 명시한다 (기본 미포함).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from corp_dl_agent.common import Quantity, StrictModel, now_iso
from corp_dl_agent.engineering import units as U
from corp_dl_agent.engineering.cad_snapshot import CadSnapshot, CadSnapshotRow, read_text_detect_encoding
from corp_dl_agent.errors import AgentError
from corp_dl_agent.version import CALCULATION_VERSION

MassStatus = Literal[
    "ok",
    "missing_density",
    "missing_volume",
    "unloaded",
    "suppressed",
    "surface_no_thickness",
    "surface_no_area",
    "missing_geometry",
    "assembly_node",
    "conflict_with_cad",
]
DensityPolicy = Literal["reject", "use_material_table"]
ScopeCategory = Literal["paint", "foam", "adhesive", "purchased"]
_SCOPE_CATEGORIES: tuple[ScopeCategory, ...] = ("paint", "foam", "adhesive", "purchased")
_SCOPE_LABEL_KO: dict[str, str] = {"paint": "도장", "foam": "폼", "adhesive": "접착제", "purchased": "미모델링 구매품"}


class ScopeItem(StrictModel):
    """CAD 에 모델링되지 않은 항목의 명시 질량(kg)."""

    name: str
    mass_kg: float = Field(ge=0)
    source_locator: str | None = None
    assumption: bool = False
    notes: str | None = None


class MassScope(StrictModel):
    """포함 범위. include_* 가 True 인데 항목이 비어 있으면 '범위에 포함되나 미정량' 으로 MISSING 처리한다."""

    include_paint: bool = False
    include_foam: bool = False
    include_adhesive: bool = False
    include_purchased: bool = False
    paint_items: list[ScopeItem] = Field(default_factory=list)
    foam_items: list[ScopeItem] = Field(default_factory=list)
    adhesive_items: list[ScopeItem] = Field(default_factory=list)
    purchased_items: list[ScopeItem] = Field(default_factory=list)

    def included(self, category: str) -> bool:
        return bool(getattr(self, f"include_{category}"))

    def items_of(self, category: str) -> list[ScopeItem]:
        return list(getattr(self, f"{category}_items"))

    def describe(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for cat, label in _SCOPE_LABEL_KO.items():
            if self.included(cat):
                n = len(self.items_of(cat))
                out[cat] = f"{label}: 포함 (명시 항목 {n}건)" if n else f"{label}: 포함 범위이나 항목 미정량 (MISSING)"
            else:
                out[cat] = f"{label}: 미포함"
        out["cad_modeled"] = "CAD 모델링 솔리드: 포함 (suppressed/assembly 노드 제외)"
        return out


class MassItem(StrictModel):
    occurrence_path: str
    reference_id: str
    quantity: int
    effective_quantity: int  # 상위 조립체 quantity 를 곱한 실제 instance 수
    unit_mass: Quantity
    total_mass: Quantity
    status: MassStatus
    cad_mass: Quantity  # CAD 가 직접 준 질량 (source) 또는 missing
    mass_check: Literal["match", "mismatch", "not_available"] = "not_available"
    material_id: str | None = None
    geometry_status: str = "solid"
    is_assembly: bool = False
    parent_path: str | None = None
    counted: bool = False  # total 에 합산되었는지
    notes: str | None = None


class ExtraMassItem(StrictModel):
    category: ScopeCategory
    name: str
    mass: Quantity


class MassResult(StrictModel):
    calculation_version: str
    computed_at: str
    source_path: str
    source_hash: str
    revisions: list[str]
    document_ids: list[str]
    scope: MassScope
    scope_summary: dict[str, str]
    items: list[MassItem]
    extra_items: list[ExtraMassItem]
    total: Quantity  # 계산 가능한 것만 합산 (CAD 부품 + scope 명시 항목)
    total_modeled: Quantity  # CAD 모델링 부품만
    complete: bool
    missing: list[str]
    warnings: list[str]
    counts: dict[str, int]
    n_instances_counted: int
    synthetic: bool = False

    def item(self, occurrence_path: str) -> MassItem:
        for it in self.items:
            if it.occurrence_path == occurrence_path:
                return it
        raise AgentError("E_INPUT_INVALID", f"occurrence_path 를 찾을 수 없습니다: {occurrence_path}")

    def items_by_status(self, status: str) -> list[MassItem]:
        return [i for i in self.items if i.status == status]


# ---------------------------------------------------------------------------
# 재료표
# ---------------------------------------------------------------------------


def load_material_table(path: str) -> dict[str, float]:
    """material_id -> density(kg/m3). CSV 열: material_id, density, density_unit."""
    import csv
    import io

    text, _enc = read_text_detect_encoding(path)
    reader = csv.DictReader(io.StringIO(text, newline=""))
    out: dict[str, float] = {}
    for idx, raw in enumerate(reader, start=1):
        row = {str(k).strip().lstrip("﻿"): (v.strip() if isinstance(v, str) else v) for k, v in raw.items() if k}
        mid = row.get("material_id")
        if not mid:
            continue
        dens = row.get("density")
        unit = row.get("density_unit")
        if dens in (None, "") or unit in (None, ""):
            raise AgentError(
                "E_INPUT_INVALID", f"재료표 행 {idx}: density/density_unit 이 없습니다", details={"material_id": mid, "row": idx}
            )
        try:
            out[mid] = U.convert_density(float(str(dens).replace(",", "")), str(unit))
        except ValueError as exc:
            raise AgentError("E_INPUT_INVALID", f"재료표 행 {idx}: density 가 숫자가 아닙니다", details={"material_id": mid}) from exc
    if not out:
        raise AgentError("E_INPUT_INVALID", "재료표에 유효한 행이 없습니다", details={"path": str(path)})
    return out


# ---------------------------------------------------------------------------
# 계산
# ---------------------------------------------------------------------------


def _q_missing(notes: str) -> Quantity:
    return Quantity.missing("kg", notes)


def _q_calc(value: float, *, locator: str, calc_id: str, assumption: bool, notes: str | None) -> Quantity:
    return Quantity(
        value=value,
        unit="kg",
        value_type="assumed" if assumption else "calculated",
        source_locator=locator,
        calculation_id=calc_id,
        calculation_version=CALCULATION_VERSION,
        assumption=assumption,
        notes=notes,
    )


def _effective_quantity(row: CadSnapshotRow, by_path: dict[str, CadSnapshotRow], warnings: list[str]) -> int:
    eff = row.quantity
    seen = {row.occurrence_path}
    parent = row.parent_path
    while parent:
        if parent in seen:
            warnings.append(f"parent_path 순환 감지: {row.occurrence_path} (상위 quantity 곱 중단)")
            break
        seen.add(parent)
        p = by_path.get(parent)
        if p is None:
            break
        eff *= p.quantity
        parent = p.parent_path
    return eff


def _resolve_density(
    row: CadSnapshotRow,
    *,
    policy: DensityPolicy,
    material_table: dict[str, float] | None,
    material_table_path: str | None,
) -> tuple[float | None, bool, str | None]:
    """(density_kg_m3, assumed, note)."""
    if row.density_kg_m3 is not None:
        return row.density_kg_m3, False, None
    if policy == "use_material_table" and material_table and row.material_id and row.material_id in material_table:
        d = material_table[row.material_id]
        loc = f"material_table:{material_table_path or ''}#{row.material_id}"
        return d, True, f"밀도 기본값 사용({row.material_id}={d:g} kg/m3, {loc})"
    return None, False, None


def _compute_row(
    row: CadSnapshotRow,
    *,
    snapshot: CadSnapshot,
    has_children: bool,
    eff_qty: int,
    policy: DensityPolicy,
    material_table: dict[str, float] | None,
    material_table_path: str | None,
    tol_rel: float,
    tol_abs: float,
    warnings: list[str],
) -> MassItem:
    locator = f"{snapshot.source_path}#{row.occurrence_path}"
    calc_id = f"mass:{row.occurrence_path}"
    cad_mass = (
        Quantity(value=row.mass_kg, unit="kg", value_type="source", source_locator=locator, notes="CAD 제공 질량")
        if row.mass_kg is not None
        else Quantity.missing("kg", "CAD 제공 질량 없음")
    )
    status: MassStatus
    unit_mass: Quantity
    assumed = False
    note_parts: list[str] = []

    if row.geometry_status == "suppressed":
        status = "suppressed"
        unit_mass = _q_missing("suppressed: 합산 제외")
    elif row.is_assembly or has_children:
        status = "assembly_node"
        if not row.is_assembly and has_children:
            warnings.append(f"{row.occurrence_path}: is_assembly=False 이지만 자식 행이 있어 총량 노드로 취급 (이중합산 방지)")
        unit_mass = _q_missing("assembly 총량 노드: 자식 부품만 합산")
    elif row.geometry_status == "unloaded":
        status = "unloaded"
        unit_mass = _q_missing("unloaded geometry: 질량 계산 불가")
    elif row.geometry_status == "missing":
        status = "missing_geometry"
        unit_mass = _q_missing("geometry missing: 질량 계산 불가")
    elif row.geometry_status == "surface":
        density, d_assumed, d_note = _resolve_density(
            row, policy=policy, material_table=material_table, material_table_path=material_table_path
        )
        if d_note:
            note_parts.append(d_note)
        area_raw = row.parameter_map.get("area")
        area_unit = row.units.get("area")
        if row.surface_thickness_m is None:
            status = "surface_no_thickness"
            unit_mass = _q_missing("surface: 두께 미지정 → 질량 생성하지 않음")
        elif not isinstance(area_raw, (int, float)) or not area_unit:
            status = "surface_no_area"
            unit_mass = _q_missing("surface: 면적(parameter_map.area + units.area) 미지정")
        elif density is None:
            status = "missing_density"
            unit_mass = _q_missing("밀도 누락 (material_id 기본값 미적용)")
        else:
            area_m2 = U.convert_area(float(area_raw), area_unit)
            value = U.surface_mass_kg(area_m2, row.surface_thickness_m, density)
            assumed = True
            note_parts.append(f"surface 근사: {area_m2:g} m2 × {row.surface_thickness_m:g} m × {density:g} kg/m3")
            status = "ok"
            unit_mass = _q_calc(value, locator=locator, calc_id=calc_id, assumption=True, notes="; ".join(note_parts))
    else:  # solid
        density, d_assumed, d_note = _resolve_density(
            row, policy=policy, material_table=material_table, material_table_path=material_table_path
        )
        if d_note:
            note_parts.append(d_note)
        if row.volume_m3 is None:
            status = "missing_volume"
            unit_mass = _q_missing("부피 누락")
        elif density is None:
            status = "missing_density"
            unit_mass = _q_missing("밀도 누락 (material_id 기본값 미적용)")
        else:
            value = U.mass_kg(row.volume_m3, density)
            assumed = d_assumed
            note_parts.append(f"{row.volume_m3:g} m3 × {density:g} kg/m3")
            status = "ok"
            unit_mass = _q_calc(value, locator=locator, calc_id=calc_id, assumption=assumed, notes="; ".join(note_parts))

    mass_check: Literal["match", "mismatch", "not_available"] = "not_available"
    if status == "ok" and cad_mass.value is not None and unit_mass.value is not None:
        diff = abs(cad_mass.value - unit_mass.value)
        if diff <= max(tol_abs, tol_rel * max(abs(cad_mass.value), abs(unit_mass.value))):
            mass_check = "match"
        else:
            mass_check = "mismatch"
            status = "conflict_with_cad"
            unit_mass = Quantity.conflict(
                "kg",
                f"계산값 {unit_mass.value:.6g} kg 과 CAD 제공값 {cad_mass.value:.6g} kg 상충 (허용오차 초과)",
            )
            warnings.append(f"{row.occurrence_path}: 계산 질량과 CAD 제공 질량 상충")

    if status == "ok" and unit_mass.value is not None:
        total = _q_calc(
            unit_mass.value * eff_qty,
            locator=locator,
            calc_id=f"{calc_id}:total",
            assumption=assumed,
            notes=f"unit {unit_mass.value:.6g} kg × {eff_qty}",
        )
        counted = True
    else:
        total = Quantity.missing("kg", unit_mass.notes)
        counted = False
    return MassItem(
        occurrence_path=row.occurrence_path,
        reference_id=row.reference_id,
        quantity=row.quantity,
        effective_quantity=eff_qty,
        unit_mass=unit_mass,
        total_mass=total,
        status=status,
        cad_mass=cad_mass,
        mass_check=mass_check,
        material_id=row.material_id,
        geometry_status=row.geometry_status,
        is_assembly=row.is_assembly,
        parent_path=row.parent_path,
        counted=counted,
        notes="; ".join(note_parts) if note_parts else None,
    )


def compute_mass(
    snapshot: CadSnapshot,
    *,
    scope: MassScope | None = None,
    density_policy: DensityPolicy = "reject",
    material_table: dict[str, float] | None = None,
    material_table_path: str | None = None,
    cad_mass_tolerance_rel: float = 0.005,
    cad_mass_tolerance_abs_kg: float = 0.001,
) -> MassResult:
    """snapshot 의 유효 솔리드 질량을 계산한다. 누락 항목은 MISSING 으로 남기고 total.complete=False."""
    scope = scope or MassScope()
    if snapshot.error_issues():
        raise AgentError(
            "E_INPUT_INVALID",
            "snapshot 에 error 급 issue 가 있어 질량을 계산하지 않습니다",
            details={"errors": [i.message for i in snapshot.error_issues()][:20]},
        )
    warnings: list[str] = []
    by_path: dict[str, CadSnapshotRow] = {r.occurrence_path: r for r in snapshot.rows}
    parents = {r.parent_path for r in snapshot.rows if r.parent_path}
    items: list[MassItem] = []
    for row in snapshot.rows:
        eff = _effective_quantity(row, by_path, warnings)
        items.append(
            _compute_row(
                row,
                snapshot=snapshot,
                has_children=row.occurrence_path in parents,
                eff_qty=eff,
                policy=density_policy,
                material_table=material_table,
                material_table_path=material_table_path,
                tol_rel=cad_mass_tolerance_rel,
                tol_abs=cad_mass_tolerance_abs_kg,
                warnings=warnings,
            )
        )

    missing: list[str] = []
    counts: dict[str, int] = {}
    for it in items:
        counts[it.status] = counts.get(it.status, 0) + 1
        if it.status not in ("ok", "suppressed", "assembly_node"):
            missing.append(f"{it.occurrence_path} ({it.status})")
    # assembly 노드 교차 검증: 자식 합계 vs CAD 제공 총량 (참고용, 합산 제외)
    for it in items:
        if it.status != "assembly_node":
            continue
        children = [c for c in items if c.parent_path == it.occurrence_path]
        if not children:
            missing.append(f"{it.occurrence_path} (assembly_node_without_children)")
            warnings.append(f"{it.occurrence_path}: assembly 노드에 자식 행이 없어 내용을 알 수 없습니다")
            continue
        if it.cad_mass.value is not None and all(c.status in ("ok", "suppressed", "assembly_node") for c in children):
            sub = sum(c.total_mass.value or 0.0 for c in children if c.counted)
            if abs(sub - it.cad_mass.value) > max(cad_mass_tolerance_abs_kg, cad_mass_tolerance_rel * abs(it.cad_mass.value)):
                warnings.append(
                    f"{it.occurrence_path}: 자식 합계 {sub:.6g} kg 과 CAD 총량 {it.cad_mass.value:.6g} kg 불일치 (총량 노드는 합산 제외)"
                )

    modeled_sum = sum(it.total_mass.value or 0.0 for it in items if it.counted)
    n_instances = sum(it.effective_quantity for it in items if it.counted)
    any_assumed = any(it.total_mass.assumption for it in items if it.counted)

    extra_items: list[ExtraMassItem] = []
    extra_sum = 0.0
    for cat in _SCOPE_CATEGORIES:
        included = scope.included(cat)
        sitems = scope.items_of(cat)
        if not included:
            if sitems:
                warnings.append(f"{_SCOPE_LABEL_KO[cat]}: include_{cat}=False 이므로 명시 항목 {len(sitems)}건은 합산하지 않습니다")
            continue
        if not sitems:
            missing.append(f"scope:{cat} (포함 범위이나 항목 미정량)")
            continue
        for s in sitems:
            q = Quantity(
                value=s.mass_kg,
                unit="kg",
                value_type="assumed" if s.assumption else "source",
                source_locator=s.source_locator,
                assumption=s.assumption,
                notes=s.notes,
            )
            extra_items.append(ExtraMassItem(category=cat, name=s.name, mass=q))
            extra_sum += s.mass_kg
            any_assumed = any_assumed or s.assumption

    complete = not missing
    total_notes = "누락 없음" if complete else "누락 " + str(len(missing)) + "건: " + ", ".join(missing)
    total = Quantity(
        value=modeled_sum + extra_sum,
        unit="kg",
        value_type="calculated",
        source_locator=snapshot.source_path,
        calculation_id="mass:total",
        calculation_version=CALCULATION_VERSION,
        assumption=any_assumed,
        notes=total_notes + ("" if complete else " (complete=False: 계산 가능한 항목만 합산)"),
    )
    total_modeled = Quantity(
        value=modeled_sum,
        unit="kg",
        value_type="calculated",
        source_locator=snapshot.source_path,
        calculation_id="mass:total_modeled",
        calculation_version=CALCULATION_VERSION,
        assumption=any(it.total_mass.assumption for it in items if it.counted),
        notes=f"CAD 모델링 부품 합계 (instance {n_instances}개)",
    )
    return MassResult(
        calculation_version=CALCULATION_VERSION,
        computed_at=now_iso(),
        source_path=snapshot.source_path,
        source_hash=snapshot.source_hash,
        revisions=snapshot.revisions(),
        document_ids=snapshot.document_ids(),
        scope=scope,
        scope_summary=scope.describe(),
        items=items,
        extra_items=extra_items,
        total=total,
        total_modeled=total_modeled,
        complete=complete,
        missing=missing,
        warnings=warnings,
        counts=counts,
        n_instances_counted=n_instances,
        synthetic=snapshot.synthetic,
    )


def mass_summary(result: MassResult) -> dict[str, Any]:
    return {
        "total_kg": result.total.value,
        "total_modeled_kg": result.total_modeled.value,
        "complete": result.complete,
        "assumption": result.total.assumption,
        "n_items": len(result.items),
        "n_instances_counted": result.n_instances_counted,
        "counts": result.counts,
        "missing": result.missing,
        "warnings": result.warnings,
        "scope": result.scope_summary,
        "calculation_version": result.calculation_version,
        "synthetic": result.synthetic,
    }
