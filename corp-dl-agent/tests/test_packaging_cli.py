"""package build/verify/lock/inventory/host-profile 와 upgrade/rollback 명령 (corp_dl_agent.cli.main 경유) 시험.

- build 는 corp_dl_agent 배포 이름/버전(__version__) 의 합성 wheel 로 수행한다 (실제 앱 wheel 빌드 없음).
- upgrade/rollback 은 scripts 가 아니라 corp_dl_agent.packaging.verifier_core 를 in-process 로 호출한다 (venv 1회).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from corp_dl_agent.cli import main
from corp_dl_agent.errors import EXIT_BLOCKED_PREREQUISITE, EXIT_RUNTIME, EXIT_USAGE, EXIT_VALIDATION
from corp_dl_agent.packaging import verifier_core as core
from corp_dl_agent.packaging.target import LINUX_X64_CP312_CPU, WIN_X64_CP312_CPU
from corp_dl_agent.version import __version__
from tests.test_packaging_kit import (
    build_fake_release,
    host_profile,
    make_fake_project,
    make_fake_wheelhouse,
    make_wheel,
    read_json,
)


def _app_like_wheel(dest: Path) -> Path:
    """배포 이름 corp-dl-agent / 버전 __version__ 인 합성 wheel (package build 의 버전 일치 검사를 통과시키기 위함)."""
    return make_wheel(
        dest,
        "corp-dl-agent",
        __version__,
        files={"corp_dl_agent_synthetic/__init__.py": "SYNTHETIC = True\n"},
        requires=("fake-dep", "fake-bin"),
        metadata_extra=("License: Proprietary - internal use",),
    )


def _json_out(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    out = capsys.readouterr().out
    return json.loads(out)


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    tmp = tmp_path_factory.mktemp("cli")
    profile = host_profile()
    project = make_fake_project(tmp / "proj")
    wheelhouse = make_fake_wheelhouse(tmp / "wheelhouse" / profile.profile_id, profile)
    app = _app_like_wheel(tmp / "dist-app")
    rc = main(
        [
            "package",
            "build",
            "--profile",
            profile.profile_id,
            "--wheelhouse",
            str(wheelhouse),
            "--app-wheel",
            str(app),
            "--out",
            str(tmp / "dist"),
            "--evidence-dir",
            str(project / "test-evidence"),
            "--package-kind",
            "CPU_OFFLINE",
            "--verification",
            "TARGET_OFFLINE_TESTED=NOT_RUN:동일 target 환경에서 최종 ZIP 설치 시험 미실행",
            "--project-root",
            str(project),
        ]
    )
    assert rc == 0
    zips = sorted((tmp / "dist").glob("*.zip"))
    assert len(zips) == 1
    return {
        "tmp": tmp,
        "profile": profile,
        "project": project,
        "wheelhouse": wheelhouse,
        "app": app,
        "zip": zips[0],
    }


def test_package_build_and_verify_cli(
    built: dict[str, Any], capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    zip_path: Path = built["zip"]
    assert zip_path.name == f"DIA_{__version__}_{built['profile'].profile_id}.zip"
    assert zip_path.with_name(zip_path.name + ".sha256").read_text(encoding="utf-8").split()[
        0
    ] == core.sha256_file(zip_path)
    capsys.readouterr()
    rc = main(["package", "verify", "--package", str(zip_path), "--install-root", str(tmp_path), "--json"])
    out = _json_out(capsys)
    assert rc == 0 and out["ok"] is True
    m = out["manifest"]
    assert (
        m["release_id"] == __version__
        and m["package_kind"] == "CPU_OFFLINE"
        and m["app_module"] == "corp_dl_agent"
    )
    assert m["verification"]["TARGET_OFFLINE_TESTED"]["status"] == "NOT_RUN"
    assert "동일 target" in m["verification"]["TARGET_OFFLINE_TESTED"]["reason"]
    assert m["verification"]["HOST_CORE_TESTED"]["status"] == "PASS"
    assert m["verification"]["CORP_INSTALLED"]["status"] == "NOT_RUN"
    assert m["schema_version"] and isinstance(m["db_schema_version"], int)
    rc = main(["package", "verify", "--package", str(zip_path)])
    text = capsys.readouterr().out
    assert rc == 0 and "결과: PASS" in text


def test_package_verify_target_profile_mismatch_cli(
    built: dict[str, Any], capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    host = built["profile"]
    other = LINUX_X64_CP312_CPU if host.os.lower().startswith("win") else WIN_X64_CP312_CPU
    prof = tmp_path / "target_profile.json"
    prof.write_text(json.dumps(other.model_dump(mode="json")), encoding="utf-8")
    rc = main(["package", "verify", "--package", str(built["zip"]), "--target-profile", str(prof), "--json"])
    out = _json_out(capsys)
    assert rc == EXIT_VALIDATION and out["ok"] is False
    assert {c["name"]: c["status"] for c in out["checks"]}["target_compat"] == "FAIL"


def test_package_build_rejects_corp_pass_and_kind_mismatch(
    built: dict[str, Any], capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    common = [
        "package",
        "build",
        "--profile",
        built["profile"].profile_id,
        "--wheelhouse",
        str(built["wheelhouse"]),
        "--app-wheel",
        str(built["app"]),
        "--out",
        str(tmp_path / "dist"),
        "--project-root",
        str(built["project"]),
        "--json",
    ]
    rc = main([*common, "--verification", "CORP_INSTALLED=PASS:사내 확인"])
    out = _json_out(capsys)
    assert rc == EXIT_VALIDATION and out["error"]["code"] == "E_PACKAGE_INVALID"
    rc = main([*common, "--package-kind", "SOURCE_ONLY"])
    out = _json_out(capsys)
    assert rc == EXIT_VALIDATION and out["error"]["details"]["derived"] == "CPU_OFFLINE"
    rc = main([*common, "--verification", "NOPE=PASS"])
    out = _json_out(capsys)
    assert rc == EXIT_USAGE and out["error"]["code"] == "E_USAGE"
    rc = main([*common, "--verification", "TARGET_OFFLINE_TESTED=MAYBE"])
    out = _json_out(capsys)
    assert rc == EXIT_USAGE
    rc = main(
        ["package", "build", "--profile", "mac-arm64-cp312-cpu", "--app-wheel", str(built["app"]), "--json"]
    )
    out = _json_out(capsys)
    assert rc == 9 and out["error"]["code"] == "E_NOT_SUPPORTED"
    assert not list((tmp_path / "dist").glob("*.zip"))


def test_package_lock_inventory_host_profile_cli(
    built: dict[str, Any], capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    rc = main(
        [
            "package",
            "lock",
            "--profile",
            built["profile"].profile_id,
            "--wheelhouse",
            str(built["wheelhouse"]),
            "--app-wheel",
            str(built["app"]),
            "--out",
            str(tmp_path / "locks"),
            "--json",
        ]
    )
    out = _json_out(capsys)
    assert (
        rc == 0
        and Path(out["lock"]).is_file()
        and Path(out["lock"]).name == f"{built['profile'].profile_id}.txt"
    )
    lock_text = Path(out["lock"]).read_text(encoding="utf-8")
    assert "corp-dl-agent==" in lock_text and "--hash=sha256:" in lock_text
    inv = tmp_path / "dependency-inventory.json"
    rc = main(
        [
            "package",
            "inventory",
            "--profile",
            built["profile"].profile_id,
            "--wheelhouse",
            str(built["wheelhouse"]),
            "--app-wheel",
            str(built["app"]),
            "--output",
            str(inv),
            "--json",
        ]
    )
    out = _json_out(capsys)
    assert rc == 0 and out["count"] == 3 and out["unknown_license_count"] == 0
    doc = read_json(inv)
    assert all(p["approval_state"] == "UNREVIEWED" for p in doc["packages"])
    rc = main(
        ["package", "inventory", "--profile", built["profile"].profile_id, "--output", str(inv), "--json"]
    )
    assert rc == EXIT_USAGE
    capsys.readouterr()
    full = tmp_path / "target_profile.json"
    minimal = tmp_path / "target_profile.min.json"
    rc = main(["package", "host-profile", "--output", str(full), "--min-output", str(minimal), "--json"])
    out = _json_out(capsys)
    assert rc == 0 and out["profile_id"] == built["profile"].profile_id
    min_doc = read_json(minimal)
    assert set(min_doc) <= set(core.MIN_PROFILE_FIELDS) and "verified_environment" in read_json(full)
    assert "verified_environment" not in min_doc and min_doc["format"] == core.MIN_PROFILE_FORMAT
    assert main(["package"]) == EXIT_USAGE


def test_upgrade_and_rollback_cli_in_process(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """upgrade 명령이 scripts 프로세스 없이 core.run_upgrade 로 새 root 에 설치·활성화하고, rollback 이 오류 안내를 낸다."""
    monkeypatch.delenv("DIA_INSTALL_ROOT", raising=False)
    monkeypatch.delenv("DIA_DATA_ROOT", raising=False)
    monkeypatch.delenv("PYTHON", raising=False)
    base_py = core.default_base_python()
    probe = core.probe_python(base_py)
    if not probe or not probe.get("has_venv") or not probe.get("has_ensurepip"):
        pytest.skip("venv/ensurepip 가 있는 base Python 없음")
    rel = build_fake_release(tmp_path, version="1.0.0")
    install_root = tmp_path / "설치 root (cli)" / "DIA"
    data_root = tmp_path / "데이터"
    # 미설치 root 에서 rollback → 대상 없음 안내 (active 변경 없음)
    rc = main(["rollback", "--to", "1.0.0", "--install-root", str(install_root), "--json"])
    out = _json_out(capsys)
    assert rc == EXIT_RUNTIME and out["error"]["code"] == "E_ROLLBACK_INCOMPATIBLE"
    # 잘못된 Python → exit 8, 손상 패키지 → exit 5 (모두 설치 시작 전)
    rc = main(
        [
            "upgrade",
            "--package",
            str(rel.zip_path),
            "--install-root",
            str(install_root),
            "--python",
            str(tmp_path / "없는 python"),
            "--json",
        ]
    )
    out = _json_out(capsys)
    assert rc == EXIT_BLOCKED_PREREQUISITE and out["error"]["code"] == "E_BLOCKED_PREREQUISITE"
    rc = main(
        [
            "upgrade",
            "--package",
            str(tmp_path / "없는.zip"),
            "--install-root",
            str(install_root),
            "--python",
            base_py,
            "--json",
        ]
    )
    out = _json_out(capsys)
    assert rc == EXIT_VALIDATION and out["error"]["code"] == "E_PACKAGE_INVALID"
    assert not (install_root / "active.json").exists()
    # 첫 upgrade = 새 설치 + 활성화 (in-process)
    rc = main(
        [
            "upgrade",
            "--package",
            str(rel.zip_path),
            "--install-root",
            str(install_root),
            "--data-root",
            str(data_root),
            "--python",
            base_py,
            "--skip-selftest",
            "--json",
        ]
    )
    out = _json_out(capsys)
    assert rc == 0, out
    assert out["status"] == "upgraded" and out["release_id"] == "1.0.0" and out["previous_release_id"] is None
    active = read_json(install_root / "active.json")
    assert (
        active["release_id"] == "1.0.0"
        and Path(active["data_root"]) == data_root
        and Path(active["venv_python"]).is_file()
    )
    assert Path(out["backup_dir"]).is_dir() and out["migration_plan"]["action"] == "none"
    # 같은 release 로 다시 → already_active, rollback --to 1.0.0 → 그대로 유지 (exit 0)
    rc = main(
        [
            "upgrade",
            "--package",
            str(rel.zip_path),
            "--install-root",
            str(install_root),
            "--python",
            base_py,
            "--skip-selftest",
            "--json",
        ]
    )
    out = _json_out(capsys)
    assert rc == 0 and out["status"] == "already_active"
    before = (install_root / "active.json").read_bytes()
    rc = main(["rollback", "--to", "1.0.0", "--install-root", str(install_root), "--json"])
    out = _json_out(capsys)
    assert rc == 0 and out["release_id"] == "1.0.0" and out["restored_db"] is None
    assert (install_root / "active.json").read_bytes() == before
    rc = main(
        [
            "rollback",
            "--to",
            "1.0.0",
            "--install-root",
            str(install_root),
            "--restore-db",
            str(tmp_path / "없는 backup"),
            "--json",
        ]
    )
    out = _json_out(capsys)
    assert rc == EXIT_RUNTIME and "backup" in out["error"]["message"]
    # 사람이 읽는 출력 (json 아님)
    rc = main(["rollback", "--to", "9.9.9", "--install-root", str(install_root)])
    err = capsys.readouterr().err
    assert rc == EXIT_RUNTIME and "E_ROLLBACK_INCOMPATIBLE" in err and "변경되지 않았습니다" in err
