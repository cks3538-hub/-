"""단위 변환 (표준 라이브러리만 사용).

지원 단위
- 부피: mm3, cm3, m3           -> m3
- 밀도: g/cm3, kg/m3            -> kg/m3
- 질량: g, kg                   -> kg
- 면적: mm2, cm2, m2            -> m2   (surface 근사 계산용)
- 길이: mm, cm, m               -> m    (surface 두께용)

검증값: 1,000,000 mm3 × 1.0 g/cm3 = 1.0 kg (계수 10^-6).
알 수 없는 단위는 E_UNIT_MISMATCH 로 거부하며 임의 추정하지 않는다.
"""

from __future__ import annotations

from corp_dl_agent.errors import AgentError

# 각 단위 -> SI 기준 단위 변환 계수 (정확한 10의 거듭제곱)
VOLUME_TO_M3: dict[str, float] = {"mm3": 1e-9, "cm3": 1e-6, "m3": 1.0}
DENSITY_TO_KG_M3: dict[str, float] = {"g/cm3": 1000.0, "kg/m3": 1.0}
MASS_TO_KG: dict[str, float] = {"g": 1e-3, "kg": 1.0}
AREA_TO_M2: dict[str, float] = {"mm2": 1e-6, "cm2": 1e-4, "m2": 1.0}
LENGTH_TO_M: dict[str, float] = {"mm": 1e-3, "cm": 1e-2, "m": 1.0}

# 표기 변형 정규화 (대소문자/기호). 지원 단위 목록 밖은 정규화하지 않는다.
_ALIASES: dict[str, str] = {
    "mm^3": "mm3",
    "mm³": "mm3",
    "cm^3": "cm3",
    "cm³": "cm3",
    "m^3": "m3",
    "m³": "m3",
    "g/cm^3": "g/cm3",
    "g/cm³": "g/cm3",
    "kg/m^3": "kg/m3",
    "kg/m³": "kg/m3",
    "mm^2": "mm2",
    "mm²": "mm2",
    "cm^2": "cm2",
    "cm²": "cm2",
    "m^2": "m2",
    "m²": "m2",
}


def normalize_unit(unit: str | None) -> str:
    if unit is None:
        return ""
    u = unit.strip().lower().replace(" ", "")
    return _ALIASES.get(u, u)


def _factor(table: dict[str, float], unit: str | None, kind: str) -> float:
    u = normalize_unit(unit)
    if not u:
        raise AgentError(
            "E_UNIT_MISMATCH",
            f"{kind} 단위가 지정되지 않았습니다.",
            details={"kind": kind, "supported": sorted(table)},
        )
    if u not in table:
        raise AgentError(
            "E_UNIT_MISMATCH",
            f"알 수 없는 {kind} 단위입니다: '{unit}'",
            details={"kind": kind, "unit": unit, "supported": sorted(table)},
        )
    return table[u]


def _check_number(value: float, kind: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AgentError("E_INPUT_INVALID", f"{kind} 값은 숫자여야 합니다.", details={"kind": kind, "value": repr(value)})
    if value != value or value in (float("inf"), float("-inf")):
        raise AgentError("E_INPUT_INVALID", f"{kind} 값이 유한하지 않습니다.", details={"kind": kind, "value": repr(value)})
    if value < 0:
        raise AgentError("E_INPUT_INVALID", f"{kind} 값은 음수일 수 없습니다.", details={"kind": kind, "value": value})
    return float(value)


def convert_volume(value: float, unit: str) -> float:
    """부피 -> m3."""
    return _check_number(value, "volume") * _factor(VOLUME_TO_M3, unit, "volume")


def convert_density(value: float, unit: str) -> float:
    """밀도 -> kg/m3."""
    return _check_number(value, "density") * _factor(DENSITY_TO_KG_M3, unit, "density")


def convert_mass(value: float, unit: str) -> float:
    """질량 -> kg."""
    return _check_number(value, "mass") * _factor(MASS_TO_KG, unit, "mass")


def convert_area(value: float, unit: str) -> float:
    """면적 -> m2."""
    return _check_number(value, "area") * _factor(AREA_TO_M2, unit, "area")


def convert_length(value: float, unit: str) -> float:
    """길이(두께) -> m."""
    return _check_number(value, "length") * _factor(LENGTH_TO_M, unit, "length")


def mass_kg(volume_m3: float, density_kg_m3: float) -> float:
    """kg = m3 × kg/m3. 입력은 이미 SI 단위여야 한다."""
    return _check_number(volume_m3, "volume_m3") * _check_number(density_kg_m3, "density_kg_m3")


def surface_mass_kg(area_m2: float, thickness_m: float, density_kg_m3: float) -> float:
    """surface 근사 질량 = m2 × m × kg/m3 (두께/밀도가 명시된 경우에만 사용)."""
    return (
        _check_number(area_m2, "area_m2")
        * _check_number(thickness_m, "thickness_m")
        * _check_number(density_kg_m3, "density_kg_m3")
    )


def supported_units() -> dict[str, list[str]]:
    return {
        "volume": sorted(VOLUME_TO_M3),
        "density": sorted(DENSITY_TO_KG_M3),
        "mass": sorted(MASS_TO_KG),
        "area": sorted(AREA_TO_M2),
        "length": sorted(LENGTH_TO_M),
    }
