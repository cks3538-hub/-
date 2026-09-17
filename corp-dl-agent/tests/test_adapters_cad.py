"""adapters.cad 시험: CSV/JSON adapter 는 실제 수입, live adapter(CATIA V5 COM / 3DEXPERIENCE) 는 INACTIVE + E_NOT_SUPPORTED."""

from __future__ import annotations

from pathlib import Path

import pytest

from corp_dl_agent.adapters.cad import (
    ADAPTERS,
    INACTIVE,
    CadAdapter,
    CatiaV5ComAdapter,
    CsvJsonCadAdapter,
    ThreeDExperienceAdapter,
    adapter_status,
    all_adapter_status,
    get_adapter,
)
from corp_dl_agent.commands.cad_cmd import live_adapter_status
from corp_dl_agent.common import Status
from corp_dl_agent.errors import AgentError

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "cad"


def test_csv_json_adapter_delegates_to_engineering_import() -> None:
    adapter = CsvJsonCadAdapter()
    assert isinstance(adapter, CadAdapter) and adapter.available() and adapter.live is False
    assert adapter.status().status == Status.PASS
    snap = adapter.extract(FIXTURES / "asm_a_snapshot.csv")
    assert len(snap.rows) == 9 and snap.synthetic is True
    cube = next(r for r in snap.rows if r.occurrence_path == "ROOT/ASM_A/PART_CUBE.1")
    assert cube.volume_m3 == pytest.approx(1e-3) and cube.density_kg_m3 == pytest.approx(1000.0)
    cp949 = adapter.extract(FIXTURES / "asm_a_snapshot_cp949.csv")
    assert cp949.encoding == "cp949"
    with pytest.raises(AgentError) as ei:
        adapter.extract(None)
    assert ei.value.code == "E_INPUT_INVALID"
    with pytest.raises(AgentError) as ei2:
        adapter.extract(FIXTURES / "does_not_exist.csv")
    assert ei2.value.code == "E_INPUT_INVALID"


@pytest.mark.parametrize("cls", [CatiaV5ComAdapter, ThreeDExperienceAdapter])
def test_live_adapters_are_inactive(cls: type) -> None:
    adapter = cls()  # 무인자 생성 (cad_cmd 탐색 계약)
    assert isinstance(adapter, CadAdapter) and adapter.live is True
    assert adapter.available() is False
    rec = adapter.status()
    assert rec.status == Status.BLOCKED and rec.reason.startswith(f"{INACTIVE}:")
    assert rec.evidence and all(e.startswith("site_checklist:") for e in rec.evidence)
    with pytest.raises(AgentError) as ei:
        adapter.extract("anything")
    err = ei.value
    assert err.code == "E_NOT_SUPPORTED" and err.details["status"] == INACTIVE
    assert err.details["adapter"] == adapter.name and err.details["site_checklist"]
    assert "cad import" in err.details["fallback"]
    desc = adapter.describe()
    assert desc["available"] is False and desc["status"]["status"] == "BLOCKED"


def test_registry_and_factory() -> None:
    assert set(ADAPTERS) == {"csv_json", "catia_v5_com", "3dexperience"}
    assert isinstance(get_adapter("catia_v5_com"), CatiaV5ComAdapter)
    assert isinstance(get_adapter("3dexperience"), ThreeDExperienceAdapter)
    assert adapter_status("csv_json").status == Status.PASS
    with pytest.raises(AgentError) as ei:
        get_adapter("solidworks")
    assert ei.value.code == "E_NOT_SUPPORTED"
    status = all_adapter_status()
    assert status["csv_json"]["available"] is True and status["catia_v5_com"]["available"] is False
    assert status["3dexperience"]["status"]["status"] == "BLOCKED"


def test_cad_cmd_discovers_adapters_via_importlib() -> None:
    for kind in ("catia_v5_com", "3dexperience"):
        rec = live_adapter_status(kind)
        assert rec.status == Status.BLOCKED and rec.reason.startswith("INACTIVE")
        assert "adapter.available() = False" in rec.reason
        assert "adapters.cad 에 해당 adapter 계약이 없습니다" not in rec.reason
        assert "로드할 수 없습니다" not in rec.reason


def test_no_invented_live_methods() -> None:
    """live adapter 는 계약(available/status/extract/describe) 외의 CATIA object model method 를 창작하지 않는다."""
    allowed = {"available", "status", "extract", "describe", "inactive_reasons"}
    for cls in (CatiaV5ComAdapter, ThreeDExperienceAdapter):
        public = {n for n in dir(cls) if not n.startswith("_") and callable(getattr(cls, n))}
        assert public <= allowed, public - allowed
