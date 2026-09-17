"""build_release → verify.py 시험: 통과 / 변조 hash / 누락 wheel / 추가 파일 / target 불일치 / traversal / symlink /
금지 패턴 / lock URL·-e 거부 / manifest·checksums 순환 없음 / inventory / package_kind / 사내 상태 PASS 금지."""

from __future__ import annotations

import json
import shutil
import stat
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from corp_dl_agent.errors import AgentError
from corp_dl_agent.packaging import verifier_core as core
from corp_dl_agent.packaging.lockfile import build_lock, check_lock_file, validate_lock_text
from corp_dl_agent.packaging.manifest import (
    CHECKSUMS_NAME,
    INVENTORY_NAME,
    MANIFEST_NAME,
    load_manifest,
    parse_checksums,
)
from corp_dl_agent.packaging.release import build_release_detailed
from corp_dl_agent.packaging.target import LINUX_X64_CP312_CPU, WIN_X64_CP312_CPU, TargetProfile
from corp_dl_agent.packaging.verify import verify_package
from tests.test_packaging_kit import (
    FAKE_MODULE,
    build_fake_release,
    host_profile,
    make_fake_app_wheel,
    make_fake_project,
    make_fake_wheelhouse,
    make_wheel,
    read_json,
    run_script,
)

Mutator = Callable[[str, bytes], bytes | None]


@pytest.fixture(scope="module")
def release(tmp_path_factory: pytest.TempPathFactory) -> Any:
    return build_fake_release(tmp_path_factory.mktemp("rel"))


def other_os_profile() -> TargetProfile:
    """호스트와 OS 가 다른 참조 프로파일 (target 불일치 시험용)."""
    host = host_profile()
    return LINUX_X64_CP312_CPU if host.os.lower().startswith("win") else WIN_X64_CP312_CPU


def rezip(
    src: Path, dst: Path, mutate: Mutator, add: dict[str, bytes] | None = None, symlink: str | None = None
) -> Path:
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = mutate(info.filename, zin.read(info))
            if data is None:
                continue
            zout.writestr(info.filename, data)
        for name, data in (add or {}).items():
            zout.writestr(name, data)
        if symlink is not None:
            zi = zipfile.ZipInfo(symlink)
            zi.external_attr = (stat.S_IFLNK | 0o777) << 16
            zout.writestr(zi, b"../../etc/passwd")
    return dst


def keep(_name: str, data: bytes) -> bytes | None:
    return data


def verify_json(pkg: Path, *extra: str) -> tuple[int, dict[str, Any]]:
    r = run_script("verify.py", "--package", str(pkg), "--json", *extra, timeout=120)
    out = json.loads(r.stdout) if r.stdout.strip().startswith("{") else {"raw": r.stdout, "stderr": r.stderr}
    return r.returncode, out


def checks(out: dict[str, Any]) -> dict[str, str]:
    return {c["name"]: c["status"] for c in out.get("checks", [])}


def detail(out: dict[str, Any], name: str) -> str:
    return next(c["detail"] for c in out["checks"] if c["name"] == name)


# --------------------------------------------------------------------------- build 결과 자체
def test_build_outputs_manifest_checksums_inventory_scope(release: Any) -> None:
    zip_path: Path = release.zip_path
    assert zip_path.name.startswith("DIA_1.0.0_") and zip_path.suffix == ".zip"
    sha_text = release.sha256_path.read_text(encoding="utf-8").split()
    assert sha_text[0] == core.sha256_file(zip_path) == release.zip_sha256 and sha_text[1] == zip_path.name
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        manifest = load_manifest(zf.read(MANIFEST_NAME))
        sums = parse_checksums(zf.read(CHECKSUMS_NAME).decode("utf-8"))
        inventory = json.loads(zf.read(INVENTORY_NAME).decode("utf-8"))
        lock_text = zf.read(manifest.lock_file).decode("utf-8")
    # 순환 없음: manifest.files 에 manifest/checksums 없음, checksums 에 manifest 있고 자기 자신 없음
    listed = {f.path for f in manifest.files}
    assert MANIFEST_NAME not in listed and CHECKSUMS_NAME not in listed
    assert MANIFEST_NAME in sums and CHECKSUMS_NAME not in sums
    assert set(sums) == (names - {CHECKSUMS_NAME})
    assert listed == names - {MANIFEST_NAME, CHECKSUMS_NAME}
    assert manifest.file_count == len(listed) and manifest.package_kind == "CPU_OFFLINE"
    assert manifest.release_id == manifest.version == "1.0.0" and manifest.app_module == FAKE_MODULE
    assert "hash 는 무결성" in manifest.hash_scope["meaning"]
    # allowlist / 제외
    for required in (
        "source/tests/test_dummy.py",
        "source/pyproject.toml",
        "scripts/install.py",
        "scripts/_common.py",
        "scripts/02_Install.cmd",
        "docs/INSTALL_KO.md",
        "docs/BUILD_STATUS.md",
        "docs/NOTICES.txt",
        "config-examples/company_config.example.yaml",
        "fixtures/README.md",
        "schemas/x.schema.json",
        "test-evidence/pytest-core.json",
        manifest.app_wheel,
    ):
        assert required in names, required
    for forbidden in (".venv", ".env", "workspace", "__pycache__", ".pyc", "test-evidence/raw"):
        assert not any(forbidden in n for n in names), forbidden
    # verification 상태
    v = {k: r.status.value for k, r in manifest.verification.items()}
    assert v["HOST_CORE_TESTED"] == "PASS" and manifest.verification["HOST_CORE_TESTED"].evidence
    assert v["TARGET_BUNDLE_PREPARED"] == "PASS" and v["TARGET_OFFLINE_TESTED"] == "NOT_RUN"
    assert v["CORP_INSTALLED"] == v["CORP_INTEGRATED"] == v["BUSINESS_VALIDATED"] == "NOT_RUN"
    assert v["TARGET_CONFIRMED"] == "TARGET_UNCONFIRMED"
    assert any("Python 런타임" in p for p in manifest.prerequisites)
    # lock: 앱 wheel 포함, hash 형식
    entries = validate_lock_text(lock_text)
    assert {e.name for e in entries} == {"fake-app", "fake-dep", "fake-bin"}
    assert all(len(e.hashes) == 1 for e in entries)
    # inventory
    pk = {p["name"]: p for p in inventory["packages"]}
    assert set(pk) == {"fake-app", "fake-dep", "fake-bin"} and inventory["count"] == 3
    assert all(p["approval_state"] == "UNREVIEWED" for p in pk.values())
    assert pk["fake-app"]["license"] == "MIT" and pk["fake-app"]["license_source"] == "License-Expression"
    assert pk["fake-dep"]["license"] == "BSD-3-Clause" and pk["fake-dep"]["home_page"].startswith("https://")
    assert pk["fake-bin"]["license_source"] == "Classifier" and "Apache" in pk["fake-bin"]["license"]
    assert pk["fake-app"]["source"] == "local-build" and pk["fake-dep"]["source"] == "pypi"
    assert inventory["unknown_license_count"] == 0


def test_verify_script_passes_for_zip_and_extracted_dir(release: Any, tmp_path: Path) -> None:
    rc, out = verify_json(release.zip_path, "--install-root", str(tmp_path))
    assert rc == 0 and out["ok"] is True, out
    st = checks(out)
    assert st["archive_safety"] == st["manifest_schema"] == st["file_list"] == st["file_hashes"] == "PASS"
    assert st["checksums"] == st["lock_text"] == st["wheelhouse_lock"] == st["target_compat"] == "PASS"
    assert st["disk_space"] in ("PASS", "NOT_RUN")
    assert out["wheel_count"] == 3
    r = run_script("verify.py", "--package", str(release.zip_path), timeout=120)
    assert r.returncode == 0 and "결과: PASS" in r.stdout
    extracted = tmp_path / "추출 폴더"
    with zipfile.ZipFile(release.zip_path) as zf:
        zf.extractall(extracted)
    rc, out = verify_json(extracted)
    assert rc == 0 and out["ok"] is True and out["is_zip"] is False
    # 상위 폴더(하위 폴더 하나) 도 허용
    rc, out = verify_json(tmp_path / "추출 폴더")
    assert rc == 0


def test_verify_tampered_file_hash_fails(release: Any, tmp_path: Path) -> None:
    bad = rezip(
        release.zip_path,
        tmp_path / "tampered.zip",
        lambda n, d: d + b"\n# tampered\n" if n == "scripts/install.py" else d,
    )
    rc, out = verify_json(bad)
    assert rc == core.EXIT_VALIDATION and out["ok"] is False
    st = checks(out)
    assert st["file_hashes"] == "FAIL" and "scripts/install.py" in detail(out, "file_hashes")
    assert st["checksums"] == "FAIL"
    r = run_script("verify.py", "--package", str(bad), timeout=120)
    assert r.returncode == core.EXIT_VALIDATION and "FAIL" in r.stdout and "다시 받으세요" in r.stderr


def test_verify_missing_wheel_fails(release: Any, tmp_path: Path) -> None:
    bad = rezip(release.zip_path, tmp_path / "missing.zip", lambda n, d: None if "fake_dep-" in n else d)
    rc, out = verify_json(bad)
    assert rc == core.EXIT_VALIDATION
    st = checks(out)
    assert st["file_list"] == "FAIL" and st["wheelhouse_lock"] == "FAIL"
    assert "fake-dep==0.1.0" in detail(out, "wheelhouse_lock") and "wheelhouse 에 없습니다" in detail(
        out, "wheelhouse_lock"
    )


def test_verify_extra_unlisted_file_fails(release: Any, tmp_path: Path) -> None:
    bad = rezip(release.zip_path, tmp_path / "extra.zip", keep, add={"scripts/extra.py": b"print(1)\n"})
    rc, out = verify_json(bad)
    assert rc == core.EXIT_VALIDATION and checks(out)["file_list"] == "FAIL"
    assert "scripts/extra.py" in detail(out, "file_list")


def test_verify_target_profile_os_abi_mismatch_fails(release: Any, tmp_path: Path) -> None:
    other = other_os_profile()
    prof_path = tmp_path / "target_profile.json"
    prof_path.write_text(json.dumps(other.model_dump(mode="json")), encoding="utf-8")
    rc, out = verify_json(release.zip_path, "--target-profile", str(prof_path))
    assert rc == core.EXIT_VALIDATION and checks(out)["target_compat"] == "FAIL"
    d = detail(out, "target_compat")
    assert "OS 불일치" in d and "platform tag" in d  # 바이너리 wheel(fake-bin) 의 platform tag 도 불일치
    # ABI 불일치 (같은 OS, 다른 Python) — 앱 쪽 verify_package 로 확인
    host = host_profile()
    abi_other = host.model_copy(
        update={
            "python_abi": "cp311",
            "python_version": "3.11",
            "profile_id": host.profile_id.replace(host.python_abi, "cp311"),
        }
    )
    report = verify_package(release.zip_path, target=abi_other)
    assert report["ok"] is False
    st = {c["name"]: c for c in report["checks"]}
    assert st["target_compat"]["status"] == "FAIL" and "ABI 불일치" in st["target_compat"]["detail"]


def test_verify_rejects_path_traversal_and_symlink(release: Any, tmp_path: Path) -> None:
    trav = rezip(release.zip_path, tmp_path / "trav.zip", keep, add={"../evil.txt": b"x"})
    r = run_script("verify.py", "--package", str(trav), "--json", timeout=120)
    assert r.returncode == core.EXIT_VALIDATION
    assert json.loads(r.stdout)["error"]["code"] == "E_PACKAGE_INVALID"
    absolute = rezip(release.zip_path, tmp_path / "abs.zip", keep, add={"/etc/evil.txt": b"x"})
    r = run_script("verify.py", "--package", str(absolute), timeout=120)
    assert r.returncode == core.EXIT_VALIDATION and "E_PACKAGE_INVALID" in r.stderr
    drive = rezip(release.zip_path, tmp_path / "drive.zip", keep, add={"C:/evil.txt": b"x"})
    with pytest.raises(AgentError) as exc:
        verify_package(drive)
    assert exc.value.code == "E_PACKAGE_INVALID"
    linked = rezip(release.zip_path, tmp_path / "link.zip", keep, symlink="scripts/evil_link")
    rc, out = verify_json(linked)
    assert rc == core.EXIT_VALIDATION and checks(out)["archive_safety"] == "FAIL"
    assert "symlink" in detail(out, "archive_safety")


def test_verify_lock_with_url_or_editable_fails(release: Any, tmp_path: Path) -> None:
    lock_rel = f"locks/{host_profile().profile_id}.txt"
    for label, extra in (
        ("url", b"requests @ https://example.invalid/requests-2.0-py3-none-any.whl\n"),
        ("editable", b"-e ./source\n"),
        ("index", b"--index-url https://pypi.org/simple\n"),
        ("vcs", b"git+https://example.invalid/x.git#egg=x\n"),
    ):
        bad = rezip(
            release.zip_path,
            tmp_path / f"lock-{label}.zip",
            lambda n, d, e=extra: d + e if n == lock_rel else d,
        )
        rc, out = verify_json(bad)
        assert rc == core.EXIT_VALIDATION, label
        st = checks(out)
        assert st["lock_text"] == "FAIL", (label, out)
        assert "허용되지 않는 항목" in detail(out, "lock_text"), label


def test_validate_lock_text_rejects_directives_and_accepts_pins() -> None:
    h = "a" * 64
    good = f"fake-dep==0.1.0 --hash=sha256:{h}\nfake_app==1.0.0 --hash=sha256:{h} --hash=sha256:{'b' * 64}\n"
    entries = validate_lock_text(good)
    assert [e.name for e in entries] == ["fake-dep", "fake-app"] and len(entries[1].hashes) == 2
    bad_lines = [
        "-e .",
        "-r other.txt",
        "-c constraints.txt",
        "--index-url https://pypi.org/simple",
        "--extra-index-url https://x.invalid",
        "--find-links ./wheels",
        "--trusted-host pypi.org",
        "git+https://example.invalid/x.git#egg=x",
        "https://example.invalid/a.whl",
        f"pkg @ file:///tmp/a.whl --hash=sha256:{h}",
        "./local/pkg",
        "C:\\wheels\\a.whl",
        f"pkg[extra]==1.0 --hash=sha256:{h}",
        f"pkg==1.0; python_version > '3' --hash=sha256:{h}",
        f"pkg==${{HOME}} --hash=sha256:{h}",
        "pkg==1.0",  # hash 없음
        "pkg>=1.0 --hash=sha256:" + h,  # pin 아님
        f"pkg==1.0 --hash=md5:{h[:32]}",
        f"pkg==1.0 --hash=sha256:{h}\npkg==1.0 --hash=sha256:{h}",  # 중복
        "",
    ]
    for line in bad_lines:
        with pytest.raises(AgentError) as exc:
            validate_lock_text(line + "\n")
        assert exc.value.code == "E_PACKAGE_INVALID", line


def test_build_lock_bidirectional_and_tag_compatibility(tmp_path: Path) -> None:
    profile = host_profile()
    wh = make_fake_wheelhouse(tmp_path / "wh", profile)
    app = make_fake_app_wheel(tmp_path / "app", "1.0.0")
    lock = build_lock(profile, wh, app, out_dir=tmp_path / "locks")
    assert lock.name == f"{profile.profile_id}.txt"
    assert check_lock_file(lock, wh, app, profile).ok
    # wheelhouse 에 lock 에 없는 wheel 추가 → 문제
    make_wheel(wh, "stray-pkg", "0.0.1", files={"stray_pkg/__init__.py": ""})
    res = check_lock_file(lock, wh, app, profile)
    assert not res.ok and any("lock 에 없습니다" in p for p in res.problems)
    (wh / "stray_pkg-0.0.1-py3-none-any.whl").unlink()
    # wheel 제거 → lock 항목에 wheel 없음
    removed = next(wh.glob("fake_dep-*.whl"))
    backup = removed.read_bytes()
    removed.unlink()
    res = check_lock_file(lock, wh, app, profile)
    assert not res.ok and any("wheelhouse 에 없습니다" in p for p in res.problems)
    removed.write_bytes(backup)
    # 내용이 바뀐 wheel → hash 불일치
    removed.write_bytes(backup + b"\0")
    res = check_lock_file(lock, wh, app, profile)
    assert not res.ok and any("sha256 이 lock 과 다릅니다" in p for p in res.problems)
    removed.write_bytes(backup)
    # tag 비호환 wheel (다른 OS 의 바이너리 wheel) → build_lock 거부
    other = other_os_profile()
    make_wheel(
        wh,
        "wrong-os",
        "1.0",
        files={"wrong_os/__init__.py": ""},
        python_tag="cp312",
        abi_tag="cp312",
        platform_tag=other.platform_tags[0],
    )
    with pytest.raises(AgentError) as exc:
        build_lock(profile, wh, app, out_dir=tmp_path / "locks2")
    assert exc.value.code == "E_PACKAGE_INVALID" and any(
        "wrong_os" in p for p in exc.value.details["problems"]
    )


@pytest.mark.parametrize(
    ("rel", "content", "why"),
    [
        ("docs/leak.md", "gateway key: sk-" + "A1b2C3d4" * 6, "API 키"),
        ("docs/personal.md", "로그 경로 /home/tester/work/x.log", "개인 절대 경로"),
        (
            "config-examples/key.txt",
            "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n",
            "개인키",
        ),
        ("docs/win.md", "C:\\Users\\hong\\Desktop\\a.txt", "개인 절대 경로"),
        ("docs/gh.md", "token ghp_" + "a1B2c3D4" * 4, "GitHub 토큰"),
        ("test-evidence/raw-copy.json", '{"log": "/Users/someone/raw.log"}', "개인 절대 경로"),
    ],
)
def test_build_rejects_forbidden_content(tmp_path: Path, rel: str, content: str, why: str) -> None:
    """allowlist 안에 들어온 파일의 내용에 secret/개인 경로가 있으면 빌드 실패 (ZIP 생성 안 함)."""
    with pytest.raises(AgentError) as exc:
        build_fake_release(tmp_path, extra_files={rel: content}, label="-forbidden")
    assert exc.value.code == "E_PACKAGE_INVALID"
    problems = "\n".join(exc.value.details["problems"])
    assert why in problems and rel in problems, problems
    assert (
        not list((tmp_path / "dist-1.0.0-forbidden").glob("*.zip"))
        if (tmp_path / "dist-1.0.0-forbidden").exists()
        else True
    )


@pytest.mark.parametrize(
    "rel",
    [
        "fixtures/.env",
        "scripts/__pycache__/x.pyc",
        "fixtures/model.pt",
        "schemas/state.sqlite",
        "fixtures/.venv/pyvenv.cfg",
        "config-examples/credentials.json",
    ],
)
def test_build_excludes_dev_artifacts_from_allowlisted_trees(tmp_path: Path, rel: str) -> None:
    """allowlist 폴더 안의 개발 산출물(.env/__pycache__/*.pyc/*.pt/*.sqlite/.venv/credentials) 은 포장에서 제외된다."""
    res = build_fake_release(tmp_path, extra_files={rel: "x"}, label="-excluded")
    with zipfile.ZipFile(res.zip_path) as zf:
        names = zf.namelist()
    assert rel not in names and not any(Path(rel).name in n for n in names), names
    assert any(rel.split("/")[-1] in sk or rel.split("/")[1] in sk for sk in res.skipped), res.skipped


def test_build_package_kind_and_corp_status_rules(tmp_path: Path) -> None:
    profile = host_profile()
    project = make_fake_project(tmp_path / "proj")
    wh = make_fake_wheelhouse(tmp_path / "wh" / profile.profile_id, profile)
    app = make_fake_app_wheel(tmp_path / "app", "1.0.0", requires=("fake-dep", "fake-bin"))
    common: dict[str, Any] = {
        "profile": profile,
        "app_wheel": app,
        "out_dir": tmp_path / "dist",
        "app_module": FAKE_MODULE,
    }
    with pytest.raises(AgentError) as exc:
        build_release_detailed(project, wheelhouse_dir=wh, package_kind="SOURCE_ONLY", **common)
    assert exc.value.code == "E_PACKAGE_INVALID" and exc.value.details["derived"] == "CPU_OFFLINE"
    with pytest.raises(AgentError) as exc:
        build_release_detailed(project, wheelhouse_dir=wh, package_kind="GPU_OFFLINE", **common)
    assert exc.value.code == "E_PACKAGE_INVALID"
    from corp_dl_agent.common import Status, StatusRecord

    with pytest.raises(AgentError) as exc:
        build_release_detailed(
            project,
            wheelhouse_dir=wh,
            verification_overrides={"CORP_INSTALLED": StatusRecord(status=Status.PASS, reason="x")},
            **common,
        )
    assert "PASS 로 표시할 수 없습니다" in exc.value.message
    # SOURCE_ONLY (wheelhouse 없음): 명시 kind 일치, offline 불가 표시
    app_only = make_fake_app_wheel(tmp_path / "app2", "1.0.0")
    res = build_release_detailed(
        project,
        profile=profile,
        wheelhouse_dir=None,
        app_wheel=app_only,
        out_dir=tmp_path / "dist-src",
        app_module=FAKE_MODULE,
        package_kind="SOURCE_ONLY",
    )
    assert res.manifest.package_kind == "SOURCE_ONLY" and res.manifest.wheel_count == 0
    assert res.manifest.verification["TARGET_BUNDLE_PREPARED"].status.value == "SOURCE_ONLY"
    assert any("SOURCE_ONLY" in w for w in res.warnings)
    assert verify_package(res.zip_path)["ok"] is True
    shutil.rmtree(tmp_path / "dist-src")


def test_build_evidence_failure_marks_host_core_fail(tmp_path: Path) -> None:
    res = build_fake_release(
        tmp_path,
        label="-evfail",
        extra_files={
            "test-evidence/mypy.json": json.dumps({"name": "mypy", "exit_code": 1, "status": "FAIL"})
        },
    )
    rec = res.manifest.verification["HOST_CORE_TESTED"]
    assert rec.status.value == "FAIL" and "mypy" in rec.reason and "test-evidence/mypy.json" in rec.evidence


def test_target_profile_min_json_has_no_forbidden_fields(tmp_path: Path) -> None:
    from corp_dl_agent.packaging.target import detect_host, write_min_profile, write_target_profile

    prof = detect_host()
    full = write_target_profile(prof, tmp_path / "target_profile.json")
    minimal = write_min_profile(prof, tmp_path / "target_profile.min.json", preflight_status="NOT_RUN")
    full_doc = read_json(full)
    assert "verified_environment" in full_doc and full_doc["target_confirmed"] is False
    min_doc = read_json(minimal)
    assert set(min_doc) <= set(core.MIN_PROFILE_FIELDS)
    assert "verified_environment" not in min_doc and "notes" not in min_doc
    text = minimal.read_text(encoding="utf-8")
    assert "python_executable" not in text and str(Path.home()) not in text and "://" not in text
    assert min_doc["python_abi"] == prof.python_abi and min_doc["target_confirmed"] is False
