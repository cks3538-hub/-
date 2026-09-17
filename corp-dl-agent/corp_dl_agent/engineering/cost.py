"""원가 계산: 공정별 recipe (사출 / 구매품 / generic).

- 모든 부품이 사출품이라고 가정하지 않는다. recipe_type 으로 공정을 명시한다.
- 사출가공비 = machine_rate_per_hour × cycle_time_s / (3600 × cavities × good_rate).
- 단가/생산량 등 입력이 없으면 해당 line 은 MISSING 이며 unit_cost 도 MISSING (complete=False). 숫자를 만들어 채우지 않는다.
- runner/regrind/setup/양품률 의 중복 반영은 recipe 검증에서 거부한다.
- 통화·기준일이 recipe 내부(구매품) 또는 호출자 기대값과 다르면 E_REVISION_MISMATCH.
- 견적가(quote) 와 제조 추정원가(manufacturing_estimate) 를 estimate_type 으로 구분한다.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, ValidationError, field_validator, model_validator

from corp_dl_agent.common import Quantity, StrictModel, now_iso, read_json, validate_strict
from corp_dl_agent.engineering.mass import MassItem, MassResult
from corp_dl_agent.errors import AgentError
from corp_dl_agent.version import CALCULATION_VERSION

EstimateType = Literal["manufacturing_estimate", "quote"]
CostCategory = Literal["material", "process", "post_process", "assembly", "purchased", "tooling", "setup", "other"]
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")

# other/generic line 이름에서 중복 반영을 의심하는 키워드
_RUNNER_WORDS = ("runner", "regrind", "scrap", "런너", "재생", "스크랩")
_SETUP_WORDS = ("setup", "set-up", "셋업", "준비", "교체")
_YIELD_WORDS = ("yield", "good_rate", "양품", "수율", "불량")
_TOOLING_WORDS = ("tooling", "mold", "금형", "상각")


def _check_currency(v: str) -> str:
    v = v.strip().upper()
    if not _CURRENCY_RE.match(v):
        raise ValueError("통화는 ISO 4217 3글자 코드여야 합니다 (예: KRW, USD)")
    return v


def _check_date(v: str) -> str:
    try:
        date.fromisoformat(v.strip())
    except ValueError as exc:
        raise ValueError("기준일은 YYYY-MM-DD 형식이어야 합니다") from exc
    return v.strip()


class PurchasedLine(StrictModel):
    name: str
    unit_price: float | None = Field(None, ge=0)
    quantity: float = Field(1.0, gt=0)  # 제품 1개당 사용 수량
    currency: str | None = None
    base_date: str | None = None
    source_locator: str | None = None

    @field_validator("currency")
    @classmethod
    def _cur(cls, v: str | None) -> str | None:
        return _check_currency(v) if v is not None else None

    @field_validator("base_date")
    @classmethod
    def _date(cls, v: str | None) -> str | None:
        return _check_date(v) if v is not None else None


class OtherLine(StrictModel):
    """명시된 기타 항목. unit: per_unit(제품 1개당) | per_kg(부품 질량 kg 당)."""

    name: str
    value: float | None = Field(None, ge=0)
    unit: Literal["per_unit", "per_kg"] = "per_unit"


class GenericLine(StrictModel):
    """generic 공정 line. unit 은 '<통화>/unit' 또는 '<통화>/kg'."""

    name: str
    value: float | None = Field(None, ge=0)
    unit: str


class _RecipeBase(StrictModel):
    name: str = ""
    currency: str
    base_date: str
    source: str | None = None
    synthetic: bool = False
    notes: str | None = None

    @field_validator("currency")
    @classmethod
    def _cur(cls, v: str) -> str:
        return _check_currency(v)

    @field_validator("base_date")
    @classmethod
    def _date(cls, v: str) -> str:
        return _check_date(v)


class InjectionMoldingRecipe(_RecipeBase):
    recipe_type: Literal["injection_molding"] = "injection_molding"
    estimate_type: EstimateType = "manufacturing_estimate"
    material_price_per_kg: float | None = Field(None, ge=0)
    cycle_time_s: float | None = Field(None, gt=0)
    cavities: int | None = Field(None, ge=1)
    good_rate: float | None = Field(None, gt=0, le=1)
    machine_rate_per_hour: float | None = Field(None, ge=0)
    runner_ratio: float = Field(0.0, ge=0, lt=1)  # runner 질량 / 제품 질량
    regrind_allowed: bool = False  # True 면 runner 재료를 재사용하므로 재료비에 runner 를 과금하지 않는다
    apply_good_rate_to_material: bool = False  # True 면 재료비에도 양품률 1회 반영 (설비비에는 항상 반영)
    post_process_cost: float | None = Field(None, ge=0)  # 제품 1개당
    assembly_cost: float | None = Field(None, ge=0)  # 제품 1개당
    purchased_items: list[PurchasedLine] = Field(default_factory=list)
    tooling_cost: float | None = Field(None, ge=0)
    tooling_amortization_qty: int | None = Field(None, ge=1)
    other: list[OtherLine] = Field(default_factory=list)
    production_volume: int | None = Field(None, ge=1)
    setup_cost_per_batch: float | None = Field(None, ge=0)
    batch_size: int | None = Field(None, ge=1)

    @model_validator(mode="after")
    def _no_double_counting(self) -> InjectionMoldingRecipe:
        names = [o.name.lower() for o in self.other]
        if self.runner_ratio > 0 and any(w in n for n in names for w in _RUNNER_WORDS):
            raise ValueError("runner/regrind 가 runner_ratio 와 other 항목에 중복 반영되었습니다")
        if self.setup_cost_per_batch is not None and any(w in n for n in names for w in _SETUP_WORDS):
            raise ValueError("setup 비용이 setup_cost_per_batch 와 other 항목에 중복 반영되었습니다")
        if self.good_rate is not None and self.good_rate < 1 and any(w in n for n in names for w in _YIELD_WORDS):
            raise ValueError("양품률/수율 손실이 good_rate 와 other 항목에 중복 반영되었습니다")
        if self.tooling_cost is not None and any(w in n for n in names for w in _TOOLING_WORDS):
            raise ValueError("금형비가 tooling_cost 와 other 항목에 중복 반영되었습니다")
        if self.regrind_allowed and self.runner_ratio == 0:
            raise ValueError("regrind_allowed=True 이면 runner_ratio 를 명시해야 합니다 (0 이면 의미 없음)")
        seen: set[str] = set()
        for n in names:
            if n in seen:
                raise ValueError(f"other 항목 이름 중복: {n}")
            seen.add(n)
        return self


class PurchasedPartRecipe(_RecipeBase):
    recipe_type: Literal["purchased_part"] = "purchased_part"
    estimate_type: EstimateType = "quote"
    unit_price: float | None = Field(None, ge=0)
    quantity_basis: int = Field(1, ge=1)  # 견적 기준 수량 (MOQ 등)
    supplier: str | None = None


class GenericProcessRecipe(_RecipeBase):
    recipe_type: Literal["generic"] = "generic"
    estimate_type: EstimateType = "manufacturing_estimate"
    lines: list[GenericLine]

    @model_validator(mode="after")
    def _lines_ok(self) -> GenericProcessRecipe:
        if not self.lines:
            raise ValueError("generic recipe 는 line 이 1개 이상이어야 합니다")
        names = [ln.name.lower() for ln in self.lines]
        if len(set(names)) != len(names):
            raise ValueError("generic line 이름이 중복됩니다")
        return self


Recipe = InjectionMoldingRecipe | PurchasedPartRecipe | GenericProcessRecipe
_RECIPE_TYPES: dict[str, type[Recipe]] = {
    "injection_molding": InjectionMoldingRecipe,
    "purchased_part": PurchasedPartRecipe,
    "generic": GenericProcessRecipe,
}


def parse_recipe(data: dict[str, Any]) -> Recipe:
    rtype = data.get("recipe_type")
    if not isinstance(rtype, str) or rtype not in _RECIPE_TYPES:
        raise AgentError(
            "E_SCHEMA_INVALID",
            f"recipe_type 이 올바르지 않습니다: {rtype!r}",
            details={"allowed": sorted(_RECIPE_TYPES)},
        )
    try:
        return validate_strict(_RECIPE_TYPES[rtype], data)
    except ValidationError as exc:
        raise AgentError(
            "E_SCHEMA_INVALID",
            f"recipe 검증 실패 ({data.get('name') or rtype})",
            details={"errors": [f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors()[:20]]},
        ) from exc


def load_recipes(path: str | Path) -> dict[str, Recipe]:
    """cost_recipes.json: {"synthetic": bool, "recipes": {name: {...recipe...}}}."""
    p = Path(path)
    if not p.is_file():
        raise AgentError("E_INPUT_INVALID", f"recipe 파일이 없습니다: {p.name}", details={"path": str(p)})
    data = read_json(p)
    if not isinstance(data, dict) or not isinstance(data.get("recipes"), dict):
        raise AgentError("E_SCHEMA_INVALID", "recipe 파일은 {'recipes': {이름: recipe}} 형식이어야 합니다", details={"path": str(p)})
    synthetic = bool(data.get("synthetic", False))
    out: dict[str, Recipe] = {}
    for name, body in data["recipes"].items():
        if not isinstance(body, dict):
            raise AgentError("E_SCHEMA_INVALID", f"recipe '{name}' 가 객체가 아닙니다")
        body = dict(body)
        body.setdefault("name", name)
        if synthetic:
            body.setdefault("synthetic", True)
        out[str(name)] = parse_recipe(body)
    return out


# ---------------------------------------------------------------------------
# 결과 모델
# ---------------------------------------------------------------------------


class CostLine(StrictModel):
    name: str
    category: CostCategory
    amount: Quantity  # unit: "<통화>/unit"
    formula: str | None = None
    inputs: dict[str, float | str | None] = Field(default_factory=dict)


class CostBreakdown(StrictModel):
    recipe_type: str
    recipe_name: str
    subject: str  # occurrence_path / "assembly_total" / "quantity"
    lines: list[CostLine]
    unit_cost: Quantity
    currency: str
    base_date: str
    quantity_basis: int
    estimate_type: EstimateType
    assumptions: list[str]
    missing: list[str]
    complete: bool
    mass_basis: Quantity
    calculation_version: str
    computed_at: str
    warnings: list[str] = Field(default_factory=list)
    synthetic: bool = False

    def line(self, name: str) -> CostLine:
        for ln in self.lines:
            if ln.name == name:
                return ln
        raise AgentError("E_INPUT_INVALID", f"cost line 을 찾을 수 없습니다: {name}")


# ---------------------------------------------------------------------------
# 계산
# ---------------------------------------------------------------------------


def _amount(value: float, currency: str, calc_id: str, *, assumption: bool = False, notes: str | None = None) -> Quantity:
    return Quantity(
        value=value,
        unit=f"{currency}/unit",
        value_type="assumed" if assumption else "calculated",
        calculation_id=calc_id,
        calculation_version=CALCULATION_VERSION,
        assumption=assumption,
        notes=notes,
    )


def _missing(currency: str, notes: str) -> Quantity:
    return Quantity.missing(f"{currency}/unit", notes)


def _mass_basis(subject: MassItem | MassResult | Quantity) -> tuple[Quantity, str]:
    if isinstance(subject, MassItem):
        return subject.unit_mass, subject.occurrence_path
    if isinstance(subject, MassResult):
        return subject.total, "assembly_total"
    return subject, "quantity"


def _check_mismatch(kind: str, expected: str | None, actual: str | None, where: str) -> None:
    if expected is not None and actual is not None and expected != actual:
        raise AgentError(
            "E_REVISION_MISMATCH",
            f"{kind} 불일치: {where} '{actual}' ≠ 기준 '{expected}'",
            details={"kind": kind, "expected": expected, "actual": actual, "where": where},
        )


def _fmt(x: float) -> str:
    return f"{x:,.6g}"


def compute_cost(
    subject: MassItem | MassResult | Quantity,
    recipe: Recipe,
    *,
    expected_currency: str | None = None,
    expected_base_date: str | None = None,
) -> CostBreakdown:
    """recipe 로 단위 원가를 계산한다. 입력이 없는 line 은 MISSING 으로 남긴다."""
    mass_q, subject_name = _mass_basis(subject)
    if mass_q.unit and mass_q.unit != "kg":
        raise AgentError("E_UNIT_MISMATCH", f"질량 기준 단위는 kg 이어야 합니다: '{mass_q.unit}'")
    _check_mismatch("통화", expected_currency.upper() if expected_currency else None, recipe.currency, "recipe")
    _check_mismatch("기준일", expected_base_date, recipe.base_date, "recipe")
    cur = recipe.currency
    lines: list[CostLine] = []
    assumptions: list[str] = []
    missing: list[str] = []
    warnings: list[str] = []
    mass_kg = mass_q.value if not mass_q.is_missing() else None
    if mass_kg is None:
        warnings.append("질량 기준값이 MISSING/CONFLICT 이므로 질량 비례 항목을 계산할 수 없습니다")
    if mass_q.assumption:
        assumptions.append("질량 기준값이 가정(assumed)을 포함합니다")
    quantity_basis = 1

    if isinstance(recipe, InjectionMoldingRecipe):
        r = recipe
        quantity_basis = r.production_volume if r.production_volume is not None else 1
        if r.production_volume is None:
            assumptions.append("생산량(production_volume) 미입력: 단가는 1개 기준, 금형상각은 tooling_amortization_qty 에 의존")
        # 재료비
        if r.material_price_per_kg is None or mass_kg is None:
            why = "material_price_per_kg 미입력" if r.material_price_per_kg is None else "질량 기준값 없음"
            lines.append(CostLine(name="재료비", category="material", amount=_missing(cur, why)))
            missing.append(f"재료비: {why}")
        else:
            runner_factor = 1.0 if r.regrind_allowed else 1.0 + r.runner_ratio
            yield_div = r.good_rate if (r.apply_good_rate_to_material and r.good_rate) else 1.0
            val = r.material_price_per_kg * mass_kg * runner_factor / yield_div
            if r.runner_ratio > 0:
                assumptions.append(
                    f"runner_ratio {r.runner_ratio:g}: "
                    + ("regrind 허용 → runner 재료 미과금" if r.regrind_allowed else f"재료비 × {runner_factor:g} (regrind 미허용)")
                )
            if r.apply_good_rate_to_material and r.good_rate:
                assumptions.append(f"재료비에 양품률 {r.good_rate:g} 1회 반영")
            else:
                assumptions.append("재료비에는 양품률 미반영 (설비비에만 반영)")
            lines.append(
                CostLine(
                    name="재료비",
                    category="material",
                    amount=_amount(val, cur, f"cost:material:{subject_name}", assumption=mass_q.assumption),
                    formula=f"{_fmt(r.material_price_per_kg)} {cur}/kg × {_fmt(mass_kg)} kg × {runner_factor:g} / {yield_div:g}",
                    inputs={
                        "material_price_per_kg": r.material_price_per_kg,
                        "mass_kg": mass_kg,
                        "runner_factor": runner_factor,
                        "yield_divisor": yield_div,
                    },
                )
            )
        # 사출가공비
        proc_inputs: dict[str, float | str | None] = {
            "machine_rate_per_hour": r.machine_rate_per_hour,
            "cycle_time_s": r.cycle_time_s,
            "cavities": r.cavities,
            "good_rate": r.good_rate,
        }
        absent = [k for k, v in proc_inputs.items() if v is None]
        if absent:
            why = "미입력: " + ", ".join(absent)
            lines.append(CostLine(name="사출가공비", category="process", amount=_missing(cur, why), inputs=proc_inputs))
            missing.append(f"사출가공비: {why}")
        else:
            assert r.machine_rate_per_hour is not None and r.cycle_time_s is not None
            assert r.cavities is not None and r.good_rate is not None
            val = r.machine_rate_per_hour * r.cycle_time_s / (3600.0 * r.cavities * r.good_rate)
            lines.append(
                CostLine(
                    name="사출가공비",
                    category="process",
                    amount=_amount(val, cur, f"cost:process:{subject_name}"),
                    formula=f"{_fmt(r.machine_rate_per_hour)} {cur}/h × {r.cycle_time_s:g} s / (3600 × {r.cavities} × {r.good_rate:g})",
                    inputs=proc_inputs,
                )
            )
        # 후가공 / 조립
        fixed_lines: list[tuple[str, CostCategory, float | None, str]] = [
            ("후가공비", "post_process", r.post_process_cost, "post_process_cost"),
            ("조립비", "assembly", r.assembly_cost, "assembly_cost"),
        ]
        for label, cat, val_opt, key in fixed_lines:
            if val_opt is None:
                lines.append(CostLine(name=label, category=cat, amount=_missing(cur, f"{key} 미입력")))
                missing.append(f"{label}: {key} 미입력")
            else:
                lines.append(
                    CostLine(
                        name=label,
                        category=cat,
                        amount=_amount(val_opt, cur, f"cost:{cat}:{subject_name}"),
                        formula=f"{_fmt(val_opt)} {cur}/unit (명시)",
                        inputs={key: val_opt},
                    )
                )
        # 구매품
        if not r.purchased_items:
            lines.append(
                CostLine(
                    name="구매품",
                    category="purchased",
                    amount=_amount(0.0, cur, f"cost:purchased:{subject_name}", notes="구매품 없음 (recipe 에 명시)"),
                    formula="0 (항목 없음)",
                )
            )
        else:
            total_p = 0.0
            p_missing: list[str] = []
            parts: list[str] = []
            for item in r.purchased_items:
                _check_mismatch("통화", cur, item.currency, f"구매품 '{item.name}'")
                _check_mismatch("기준일", r.base_date, item.base_date, f"구매품 '{item.name}'")
                if item.unit_price is None:
                    p_missing.append(item.name)
                    continue
                total_p += item.unit_price * item.quantity
                parts.append(f"{item.name} {_fmt(item.unit_price)} × {item.quantity:g}")
            if p_missing:
                why = "단가 미입력: " + ", ".join(p_missing)
                lines.append(CostLine(name="구매품", category="purchased", amount=_missing(cur, why)))
                missing.append(f"구매품: {why}")
            else:
                lines.append(
                    CostLine(
                        name="구매품",
                        category="purchased",
                        amount=_amount(total_p, cur, f"cost:purchased:{subject_name}"),
                        formula=" + ".join(parts),
                        inputs={"n_items": float(len(r.purchased_items))},
                    )
                )
        # 금형상각
        amort_qty = r.tooling_amortization_qty if r.tooling_amortization_qty is not None else r.production_volume
        if r.tooling_cost is None:
            lines.append(CostLine(name="금형상각", category="tooling", amount=_missing(cur, "tooling_cost 미입력")))
            missing.append("금형상각: tooling_cost 미입력")
        elif amort_qty is None:
            why = "상각 수량 없음 (tooling_amortization_qty/production_volume 미입력)"
            lines.append(CostLine(name="금형상각", category="tooling", amount=_missing(cur, why)))
            missing.append(f"금형상각: {why}")
        else:
            if r.tooling_amortization_qty is None:
                assumptions.append(f"금형상각 수량 = production_volume ({amort_qty})")
            val = r.tooling_cost / amort_qty
            lines.append(
                CostLine(
                    name="금형상각",
                    category="tooling",
                    amount=_amount(val, cur, f"cost:tooling:{subject_name}"),
                    formula=f"{_fmt(r.tooling_cost)} {cur} / {amort_qty}",
                    inputs={"tooling_cost": r.tooling_cost, "amortization_qty": amort_qty},
                )
            )
        # setup
        if r.setup_cost_per_batch is None:
            lines.append(CostLine(name="setup", category="setup", amount=_missing(cur, "setup_cost_per_batch 미입력")))
            missing.append("setup: setup_cost_per_batch 미입력")
        elif r.batch_size is None:
            lines.append(CostLine(name="setup", category="setup", amount=_missing(cur, "batch_size 미입력")))
            missing.append("setup: batch_size 미입력")
        else:
            val = r.setup_cost_per_batch / r.batch_size
            lines.append(
                CostLine(
                    name="setup",
                    category="setup",
                    amount=_amount(val, cur, f"cost:setup:{subject_name}"),
                    formula=f"{_fmt(r.setup_cost_per_batch)} {cur}/batch / {r.batch_size}",
                    inputs={"setup_cost_per_batch": r.setup_cost_per_batch, "batch_size": r.batch_size},
                )
            )
        # 기타 (명시)
        for o in r.other:
            if o.value is None:
                lines.append(CostLine(name=o.name, category="other", amount=_missing(cur, "value 미입력")))
                missing.append(f"{o.name}: value 미입력")
            elif o.unit == "per_kg":
                if mass_kg is None:
                    lines.append(CostLine(name=o.name, category="other", amount=_missing(cur, "질량 기준값 없음")))
                    missing.append(f"{o.name}: 질량 기준값 없음")
                else:
                    lines.append(
                        CostLine(
                            name=o.name,
                            category="other",
                            amount=_amount(o.value * mass_kg, cur, f"cost:other:{o.name}", assumption=mass_q.assumption),
                            formula=f"{_fmt(o.value)} {cur}/kg × {_fmt(mass_kg)} kg",
                            inputs={"value_per_kg": o.value, "mass_kg": mass_kg},
                        )
                    )
            else:
                lines.append(
                    CostLine(
                        name=o.name,
                        category="other",
                        amount=_amount(o.value, cur, f"cost:other:{o.name}"),
                        formula=f"{_fmt(o.value)} {cur}/unit (명시)",
                        inputs={"value": o.value},
                    )
                )
    elif isinstance(recipe, PurchasedPartRecipe):
        quantity_basis = recipe.quantity_basis
        if recipe.unit_price is None:
            lines.append(CostLine(name="구매 단가", category="purchased", amount=_missing(cur, "unit_price 미입력")))
            missing.append("구매 단가: unit_price 미입력")
        else:
            lines.append(
                CostLine(
                    name="구매 단가",
                    category="purchased",
                    amount=_amount(recipe.unit_price, cur, f"cost:purchased:{subject_name}"),
                    formula=f"{_fmt(recipe.unit_price)} {cur}/unit ({recipe.estimate_type}, 기준 수량 {recipe.quantity_basis})",
                    inputs={"unit_price": recipe.unit_price, "supplier": recipe.supplier},
                )
            )
    else:
        for ln in recipe.lines:
            unit = ln.unit.strip()
            if "/" not in unit:
                raise AgentError("E_UNIT_MISMATCH", f"generic line '{ln.name}' 단위는 '<통화>/unit' 또는 '<통화>/kg' 이어야 합니다: '{ln.unit}'")
            line_cur, per = unit.split("/", 1)
            line_cur = line_cur.upper()
            _check_mismatch("통화", cur, line_cur, f"generic line '{ln.name}'")
            if per not in ("unit", "kg"):
                raise AgentError("E_UNIT_MISMATCH", f"generic line '{ln.name}' 단위 분모는 unit 또는 kg 만 허용됩니다: '{ln.unit}'")
            if ln.value is None:
                lines.append(CostLine(name=ln.name, category="other", amount=_missing(cur, "value 미입력")))
                missing.append(f"{ln.name}: value 미입력")
            elif per == "kg":
                if mass_kg is None:
                    lines.append(CostLine(name=ln.name, category="other", amount=_missing(cur, "질량 기준값 없음")))
                    missing.append(f"{ln.name}: 질량 기준값 없음")
                else:
                    lines.append(
                        CostLine(
                            name=ln.name,
                            category="other",
                            amount=_amount(ln.value * mass_kg, cur, f"cost:generic:{ln.name}", assumption=mass_q.assumption),
                            formula=f"{_fmt(ln.value)} {cur}/kg × {_fmt(mass_kg)} kg",
                            inputs={"value_per_kg": ln.value, "mass_kg": mass_kg},
                        )
                    )
            else:
                lines.append(
                    CostLine(
                        name=ln.name,
                        category="other",
                        amount=_amount(ln.value, cur, f"cost:generic:{ln.name}"),
                        formula=f"{_fmt(ln.value)} {cur}/unit (명시)",
                        inputs={"value": ln.value},
                    )
                )

    complete = not missing
    any_assumed = any(ln.amount.assumption for ln in lines)
    if complete:
        total = sum(ln.amount.value or 0.0 for ln in lines)
        unit_cost = Quantity(
            value=total,
            unit=f"{cur}/unit",
            value_type="assumed" if any_assumed else "calculated",
            calculation_id=f"cost:unit_cost:{subject_name}",
            calculation_version=CALCULATION_VERSION,
            assumption=any_assumed,
            notes=f"{recipe.estimate_type}; line 합계 {len(lines)}건",
        )
    else:
        unit_cost = _missing(cur, "MISSING line 있음: " + "; ".join(missing))
    return CostBreakdown(
        recipe_type=recipe.recipe_type,
        recipe_name=recipe.name,
        subject=subject_name,
        lines=lines,
        unit_cost=unit_cost,
        currency=cur,
        base_date=recipe.base_date,
        quantity_basis=quantity_basis,
        estimate_type=recipe.estimate_type,
        assumptions=assumptions,
        missing=missing,
        complete=complete,
        mass_basis=mass_q,
        calculation_version=CALCULATION_VERSION,
        computed_at=now_iso(),
        warnings=warnings,
        synthetic=recipe.synthetic,
    )


def cost_summary(b: CostBreakdown) -> dict[str, Any]:
    return {
        "recipe": b.recipe_name,
        "recipe_type": b.recipe_type,
        "subject": b.subject,
        "unit_cost": b.unit_cost.value,
        "currency": b.currency,
        "base_date": b.base_date,
        "estimate_type": b.estimate_type,
        "complete": b.complete,
        "assumption": b.unit_cost.assumption,
        "lines": {ln.name: ln.amount.value for ln in b.lines},
        "missing": b.missing,
        "assumptions": b.assumptions,
        "synthetic": b.synthetic,
    }
