"""단위 변환 정확값 시험 (BUILD_SPEC [7]: 1,000,000 mm3 × 1.0 g/cm3 = 1.0 kg)."""

from __future__ import annotations

import math

import pytest

from corp_dl_agent.engineering import units as U
from corp_dl_agent.errors import AgentError


def test_known_mass_one_kg() -> None:
    v = U.convert_volume(1_000_000, "mm3")
    d = U.convert_density(1.0, "g/cm3")
    assert v == pytest.approx(1e-3, abs=1e-18)
    assert d == 1000.0
    assert U.mass_kg(v, d) == pytest.approx(1.0, abs=1e-12)


@pytest.mark.parametrize(
    ("value", "unit", "expected"),
    [
        (1.0, "m3", 1.0),
        (1.0, "cm3", 1e-6),
        (1.0, "mm3", 1e-9),
        (250.0, "cm3", 2.5e-4),
        (1.0, "mm³", 1e-9),
        (1.0, "MM3", 1e-9),
        (1.0, " cm^3 ", 1e-6),
        (0.0, "mm3", 0.0),
    ],
)
def test_convert_volume(value: float, unit: str, expected: float) -> None:
    assert U.convert_volume(value, unit) == pytest.approx(expected, rel=1e-12, abs=1e-24)


@pytest.mark.parametrize(
    ("value", "unit", "expected"),
    [(1.0, "g/cm3", 1000.0), (2700.0, "kg/m3", 2700.0), (1.37, "g/cm³", 1370.0), (7.85, "G/CM3", 7850.0)],
)
def test_convert_density(value: float, unit: str, expected: float) -> None:
    assert U.convert_density(value, unit) == pytest.approx(expected, rel=1e-12)


@pytest.mark.parametrize(("value", "unit", "expected"), [(1000.0, "g", 1.0), (2.5, "kg", 2.5), (0.0, "g", 0.0)])
def test_convert_mass(value: float, unit: str, expected: float) -> None:
    assert U.convert_mass(value, unit) == pytest.approx(expected, rel=1e-12)


def test_convert_area_and_length() -> None:
    assert U.convert_area(200_000, "mm2") == pytest.approx(0.2, rel=1e-12)
    assert U.convert_area(1.0, "cm2") == pytest.approx(1e-4, rel=1e-12)
    assert U.convert_length(2.0, "mm") == pytest.approx(2e-3, rel=1e-12)
    assert U.convert_length(1.0, "m") == 1.0
    assert U.surface_mass_kg(0.2, 0.002, 1200.0) == pytest.approx(0.48, rel=1e-12)


@pytest.mark.parametrize(
    ("func", "unit"),
    [
        (U.convert_volume, "in3"),
        (U.convert_volume, "liter"),
        (U.convert_density, "lb/ft3"),
        (U.convert_mass, "lb"),
        (U.convert_area, "in2"),
        (U.convert_length, "inch"),
    ],
)
def test_unknown_unit_rejected(func, unit: str) -> None:
    with pytest.raises(AgentError) as ei:
        func(1.0, unit)
    assert ei.value.code == "E_UNIT_MISMATCH"
    assert "supported" in ei.value.details


def test_missing_unit_rejected() -> None:
    with pytest.raises(AgentError) as ei:
        U.convert_volume(1.0, "")
    assert ei.value.code == "E_UNIT_MISMATCH"


def test_negative_and_non_finite_rejected() -> None:
    with pytest.raises(AgentError) as ei:
        U.convert_volume(-1.0, "mm3")
    assert ei.value.code == "E_INPUT_INVALID"
    with pytest.raises(AgentError):
        U.convert_density(math.nan, "g/cm3")
    with pytest.raises(AgentError):
        U.mass_kg(math.inf, 1000.0)
    with pytest.raises(AgentError):
        U.convert_mass(True, "kg")  # type: ignore[arg-type]


def test_supported_units_listing() -> None:
    s = U.supported_units()
    assert s["volume"] == ["cm3", "m3", "mm3"]
    assert s["density"] == ["g/cm3", "kg/m3"]
    assert s["mass"] == ["g", "kg"]
