"""self-test: 설치 자체 검증. 항목별 PASS/FAIL/NOT_RUN. 필수 항목이 모두 PASS 면 exit 0, 아니면 5.

설치기(scripts/install.py)가 active.json 전환 전에 실행한다.
"""

from __future__ import annotations

import importlib
import json
import math
import tempfile
import time
from pathlib import Path
from typing import Any

from corp_dl_agent.common import (
    Quantity,
    StrictModel,
    atomic_write_json,
    read_json,
    sha256_text,
    validate_strict,
)
from corp_dl_agent.config.schemas import AppConfig
from corp_dl_agent.errors import EXIT_VALIDATION, AgentError
from corp_dl_agent.security.paths import resolve_within, safe_member_path
from corp_dl_agent.version import __version__


class SelfTestItem(StrictModel):
    name: str
    required: bool
    status: str  # PASS | FAIL | NOT_RUN
    detail: str = ""
    elapsed_ms: int = 0


def _run(name: str, required: bool, fn: Any) -> SelfTestItem:
    t0 = time.perf_counter()
    try:
        detail = fn()
        return SelfTestItem(
            name=name,
            required=required,
            status="PASS",
            detail=str(detail or ""),
            elapsed_ms=int((time.perf_counter() - t0) * 1000),
        )
    except _NotRun as nr:
        return SelfTestItem(
            name=name,
            required=required,
            status="NOT_RUN",
            detail=str(nr),
            elapsed_ms=int((time.perf_counter() - t0) * 1000),
        )
    except Exception as exc:  # noqa: BLE001 - 항목 실패를 기록한다
        return SelfTestItem(
            name=name,
            required=required,
            status="FAIL",
            detail=f"{type(exc).__name__}: {str(exc)[:300]}",
            elapsed_ms=int((time.perf_counter() - t0) * 1000),
        )


class _NotRun(Exception):
    pass


def _t_config(cfg: AppConfig) -> str:
    return f"profile={cfg.profile.value}, network_allowed={cfg.network_allowed()}"


def _t_strict_reject() -> str:
    try:
        validate_strict(AppConfig, {"profile": "corp-offline", "unknown_key": 1})
    except Exception:
        return "extra 필드 거부 확인"
    raise AssertionError("extra 필드가 거부되지 않음")


def _t_units() -> str:
    try:
        units = importlib.import_module("corp_dl_agent.engineering.units")
    except ImportError as exc:
        raise _NotRun(f"engineering.units 없음: {exc}") from exc
    kg = units.mass_kg(units.convert_volume(1_000_000, "mm3"), units.convert_density(1.0, "g/cm3"))
    if not math.isclose(kg, 1.0, rel_tol=1e-12):
        raise AssertionError(f"1,000,000 mm3 × 1.0 g/cm3 = {kg} kg (기대 1.0)")
    return "1,000,000 mm3 × 1.0 g/cm3 = 1.0 kg"


def _t_fixture_mass() -> str:
    try:
        eng = importlib.import_module("corp_dl_agent.engineering")
    except ImportError as exc:
        raise _NotRun(f"engineering 없음: {exc}") from exc
    fx = Path(__file__).resolve().parent / "defaults" / "selftest_snapshot.csv"
    if not fx.is_file():
        raise _NotRun("내장 selftest_snapshot.csv 없음")
    snap = eng.import_snapshot(fx)
    res = eng.compute_mass(snap)
    item = res.item("ROOT/ASM_A/PART_CUBE.1")
    if (
        item is None
        or item.unit_mass.value is None
        or not math.isclose(item.unit_mass.value, 1.0, rel_tol=1e-9)
    ):
        raise AssertionError(f"PART_CUBE unit_mass={getattr(item, 'unit_mass', None)}")
    return f"asm_a PART_CUBE.1 unit_mass={item.unit_mass.value} kg, total={res.total.value}"


def _t_sqlite(tmp: Path) -> str:
    from corp_dl_agent.state.db import StateDB

    db = StateDB(tmp / "state" / "selftest.sqlite")
    try:
        with db.transaction() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS t (k TEXT PRIMARY KEY, v TEXT)")
            conn.execute("INSERT OR REPLACE INTO t VALUES ('a', 'b')")
        row = db.query_one("SELECT v FROM t WHERE k='a'")
        assert row is not None and row[0] == "b"
    finally:
        db.close()
    return "WAL sqlite 쓰기/읽기"


def _t_atomic(tmp: Path) -> str:
    p = tmp / "한글 폴더 이름" / "원자적 쓰기.json"
    atomic_write_json(p, {"ok": True, "한글": "값"})
    d = read_json(p)
    assert d["ok"] is True and d["한글"] == "값"
    return f"한글/공백 경로 원자적 쓰기: {p.name}"


def _t_paths(tmp: Path) -> str:
    inside = tmp / "in" / "x.txt"
    inside.parent.mkdir(parents=True, exist_ok=True)
    inside.write_text("x", encoding="utf-8")
    resolve_within(inside, [tmp])
    try:
        resolve_within(tmp.parent / "outside.txt", [tmp])
    except AgentError as exc:
        assert exc.code == "E_PATH_OUTSIDE_ROOT"
    else:
        raise AssertionError("root 밖 경로가 허용됨")
    for bad in ("../x", "/abs", "C:/x"):
        try:
            safe_member_path(bad)
        except AgentError:
            continue
        raise AssertionError(f"traversal 허용: {bad}")
    return "root 이탈/traversal 거부"


def _t_quantity() -> str:
    q = Quantity(value=1.5, unit="kg", value_type="calculated", calculation_id="c1")
    m = Quantity.missing("kg")
    assert not q.is_missing() and m.is_missing()
    return "Quantity provenance"


def _t_documents_libs() -> str:
    pptx = importlib.import_module("pptx")
    openpyxl = importlib.import_module("openpyxl")
    return f"python-pptx {getattr(pptx, '__version__', '?')}, openpyxl {openpyxl.__version__}"


def _t_ml_libs() -> str:
    np = importlib.import_module("numpy")
    pd = importlib.import_module("pandas")
    sk = importlib.import_module("sklearn")
    return f"numpy {np.__version__}, pandas {pd.__version__}, scikit-learn {sk.__version__}"


def _t_torch_cpu() -> str:
    torch = importlib.import_module("torch")
    x = torch.ones(4, 3)
    w = torch.nn.Linear(3, 1)
    y = w(x)
    assert y.shape == (4, 1) and bool(torch.isfinite(y).all())
    return f"torch {torch.__version__} CPU 연산 OK (cuda={torch.cuda.is_available()})"


def _t_hash() -> str:
    assert sha256_text("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    return "sha256 검증"


def run_selftest(
    cfg: AppConfig, *, require_ml: bool = True, require_documents: bool = True
) -> dict[str, Any]:
    items: list[SelfTestItem] = []
    with tempfile.TemporaryDirectory(prefix="dia-selftest-") as td:
        tmp = Path(td)
        items.append(_run("config", True, lambda: _t_config(cfg)))
        items.append(_run("strict_schema_reject", True, _t_strict_reject))
        items.append(_run("hash", True, _t_hash))
        items.append(_run("quantity", True, _t_quantity))
        items.append(_run("units_1kg", True, _t_units))
        items.append(_run("fixture_mass", False, _t_fixture_mass))
        items.append(_run("sqlite", True, lambda: _t_sqlite(tmp)))
        items.append(_run("atomic_write_korean_path", True, lambda: _t_atomic(tmp)))
        items.append(_run("path_isolation", True, lambda: _t_paths(tmp)))
        items.append(_run("documents_libs", require_documents, _t_documents_libs))
        items.append(_run("ml_libs", require_ml, _t_ml_libs))
        items.append(_run("torch_cpu", require_ml, _t_torch_cpu))
    required_fail = [i.name for i in items if i.required and i.status != "PASS"]
    return {
        "version": __version__,
        "ok": not required_fail,
        "exit_code": 0 if not required_fail else EXIT_VALIDATION,
        "required_failures": required_fail,
        "items": [i.model_dump(mode="json") for i in items],
    }


def format_selftest_ko(result: dict[str, Any]) -> str:
    lines = [f"self-test corp-dl-agent {result['version']}: {'PASS' if result['ok'] else 'FAIL'}"]
    for it in result["items"]:
        mark = {"PASS": "✔", "FAIL": "✘", "NOT_RUN": "·"}.get(it["status"], "?")
        req = "필수" if it["required"] else "선택"
        lines.append(f"  {mark} {it['name']:26s} [{req}] {it['status']:7s} {it['detail']}")
    if result["required_failures"]:
        lines.append("실패한 필수 항목: " + ", ".join(result["required_failures"]))
    return "\n".join(lines)


def write_selftest_result(result: dict[str, Any], path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
