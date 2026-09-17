"""packaging/scripts 시험 공용 도구.

- setuptools 없이 zipfile 로 유효한 wheel(METADATA/WHEEL/RECORD 포함)을 만든다.
- 가짜 프로젝트/가짜 wheelhouse/가짜 앱(fake_app) 으로 release ZIP 을 만든다 (실제 corp_dl_agent wheel 은 빌드하지 않는다).
- 스크립트(scripts/*.py) 를 subprocess 로 실행하는 helper.
이 파일의 시험은 도구 자체의 유효성만 확인한다.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
FAKE_MODULE = "fake_app"
FAKE_DIST = "fake-app"

FAKE_APP_MAIN = '''"""합성 시험용 가짜 앱 (corp_dl_agent 가 아님)."""
import json
import os
import sys

VERSION = "{version}"
SELFTEST_OK = {selftest_ok}


def main(argv):
    if not argv:
        print("fake_app menu")
        return 0
    cmd = argv[0]
    if cmd == "version":
        print(json.dumps({{"version": VERSION, "synthetic": True}}))
        return 0
    if cmd == "self-test":
        res = {{"version": VERSION, "ok": SELFTEST_OK, "exit_code": 0 if SELFTEST_OK else 5, "args": argv[1:], "synthetic": True}}
        if "--output" in argv:
            with open(argv[argv.index("--output") + 1], "w", encoding="utf-8") as f:
                json.dump(res, f)
        print(json.dumps(res))
        return 0 if SELFTEST_OK else 5
    if cmd == "echo":
        print(json.dumps({{"args": argv[1:], "cwd": os.getcwd(), "env_install_root": os.environ.get("DIA_INSTALL_ROOT")}}))
        return 0
    print("unknown command: " + cmd, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
'''


def _b64(digest: bytes) -> str:
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def make_wheel(
    dest_dir: Path,
    name: str,
    version: str,
    *,
    files: dict[str, str | bytes],
    python_tag: str = "py3",
    abi_tag: str = "none",
    platform_tag: str = "any",
    requires: tuple[str, ...] = (),
    metadata_extra: tuple[str, ...] = (),
) -> Path:
    """유효한 wheel 을 zipfile 로 직접 생성한다 (결정적: 고정 timestamp)."""
    dist = name.replace("-", "_")
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / f"{dist}-{version}-{python_tag}-{abi_tag}-{platform_tag}.whl"
    dist_info = f"{dist}-{version}.dist-info"
    meta = [
        "Metadata-Version: 2.1",
        f"Name: {name}",
        f"Version: {version}",
        "Summary: synthetic test wheel (data_origin=synthetic)",
        *[f"Requires-Dist: {r}" for r in requires],
        *metadata_extra,
        "",
        "synthetic test wheel body",
    ]
    wheel = [
        "Wheel-Version: 1.0",
        "Generator: corp-dl-agent-tests",
        f"Root-Is-Purelib: {'true' if platform_tag == 'any' else 'false'}",
        f"Tag: {python_tag}-{abi_tag}-{platform_tag}",
        "",
    ]
    entries: dict[str, str | bytes] = dict(files)
    entries[f"{dist_info}/METADATA"] = "\n".join(meta) + "\n"
    entries[f"{dist_info}/WHEEL"] = "\n".join(wheel)
    top = sorted(
        {p.split("/")[0] for p in files if "/" in p}
        | {p[:-3] for p in files if "/" not in p and p.endswith(".py")}
    )
    entries[f"{dist_info}/top_level.txt"] = "\n".join(top) + "\n"
    record: list[str] = []
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, content in entries.items():
            data = content.encode("utf-8") if isinstance(content, str) else content
            zf.writestr(zipfile.ZipInfo(rel, date_time=(2020, 1, 1, 0, 0, 0)), data)
            record.append(f"{rel},sha256={_b64(hashlib.sha256(data).digest())},{len(data)}")
        record.append(f"{dist_info}/RECORD,,")
        zf.writestr(
            zipfile.ZipInfo(f"{dist_info}/RECORD", date_time=(2020, 1, 1, 0, 0, 0)), "\n".join(record) + "\n"
        )
    return path


def make_fake_app_wheel(
    dest_dir: Path, version: str, *, selftest_ok: bool = True, requires: tuple[str, ...] = ()
) -> Path:
    main_src = FAKE_APP_MAIN.format(version=version, selftest_ok="True" if selftest_ok else "False")
    return make_wheel(
        dest_dir,
        FAKE_DIST,
        version,
        files={
            f"{FAKE_MODULE}/__init__.py": f'"""fake app"""\n__version__ = "{version}"\n',
            f"{FAKE_MODULE}/__main__.py": main_src,
        },
        requires=requires,
        metadata_extra=("License-Expression: MIT", "Home-page: https://example.invalid/fake-app"),
    )


def host_profile() -> Any:
    from corp_dl_agent.packaging.target import detect_host

    return detect_host()


def binary_platform_tag(profile: Any) -> str:
    """profile 에 호환되는 '바이너리' wheel platform tag 를 고른다 (시험용)."""
    first = profile.platform_tags[0]
    if first.startswith("win"):
        return first
    if first.startswith("manylinux"):
        from corp_dl_agent.packaging import verifier_core as core

        arch = core._parse_platform_tag(first)[2]  # manylinux_2_39_x86_64 -> x86_64
        return f"manylinux_2_17_{arch}"
    if first.startswith("macosx"):
        return first
    return "any"


def make_fake_wheelhouse(dest_dir: Path, profile: Any, *, with_binary: bool = True) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    make_wheel(
        dest_dir,
        "fake-dep",
        "0.1.0",
        files={"fake_dep/__init__.py": "VALUE = 1\n"},
        metadata_extra=("License: BSD-3-Clause", "Project-URL: Homepage, https://example.invalid/fake-dep"),
    )
    if with_binary:
        abi = profile.python_abi
        make_wheel(
            dest_dir,
            "fake-bin",
            "0.2.0",
            files={"fake_bin/__init__.py": "VALUE = 2\n"},
            python_tag=abi,
            abi_tag=abi,
            platform_tag=binary_platform_tag(profile),
            metadata_extra=("Classifier: License :: OSI Approved :: Apache Software License",),
        )
    return dest_dir


def make_fake_project(root: Path, *, extra_files: dict[str, str] | None = None) -> Path:
    """allowlist 포장 검사용 가짜 프로젝트. 제외되어야 할 항목(.venv/.env/workspace/__pycache__)도 함께 만든다."""
    (root / FAKE_MODULE).mkdir(parents=True, exist_ok=True)
    (root / FAKE_MODULE / "__init__.py").write_text('"""fake source for review"""\n', encoding="utf-8")
    (root / FAKE_MODULE / "__pycache__").mkdir(exist_ok=True)
    (root / FAKE_MODULE / "__pycache__" / "x.cpython-312.pyc").write_bytes(b"\x00")
    (root / "tests").mkdir(exist_ok=True)
    (root / "tests" / "test_dummy.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        '[project]\nname = "fake-app"\nversion = "0.0.0"\n', encoding="utf-8"
    )
    (root / "README.md").write_text("# fake project (synthetic)\n", encoding="utf-8")
    (root / "CLAUDE.md").write_text("# rules\n", encoding="utf-8")
    (root / "docs").mkdir(exist_ok=True)
    (root / "docs" / "CONTRACT.md").write_text("# contract\n", encoding="utf-8")
    (root / "docs" / "INSTALL_KO.md").write_text(
        "설치 폴더 예시: D:\\설계 자동화\\DIA (허용되는 예시 경로)\n", encoding="utf-8"
    )
    (root / "docs" / "NOTICES.txt").write_text("notices\n", encoding="utf-8")
    shutil.copytree(SCRIPTS_DIR, root / "scripts", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (root / "config-examples").mkdir(exist_ok=True)
    (root / "config-examples" / "company_config.example.yaml").write_text(
        "profile: corp-offline\ngateway:\n  secret_ref: null\n", encoding="utf-8"
    )
    (root / "fixtures").mkdir(exist_ok=True)
    (root / "fixtures" / "README.md").write_text("synthetic: true\n", encoding="utf-8")
    (root / "schemas").mkdir(exist_ok=True)
    (root / "schemas" / "x.schema.json").write_text("{}\n", encoding="utf-8")
    (root / "test-evidence" / "raw").mkdir(parents=True, exist_ok=True)
    (root / "test-evidence" / "pytest-core.json").write_text(
        json.dumps(
            {
                "name": "pytest-core",
                "command": "python -m pytest -q",
                "cwd": "<PROJECT_ROOT>",
                "exit_code": 0,
                "status": "PASS",
            }
        ),
        encoding="utf-8",
    )
    (root / "test-evidence" / "raw" / "pytest-core.log").write_text(
        "raw log with personal path /ho" + "me/tester/x\n", encoding="utf-8"
    )
    (root / ".venv").mkdir(exist_ok=True)
    (root / ".venv" / "pyvenv.cfg").write_text("home = /usr\n", encoding="utf-8")
    (root / ".env").write_text("SECRET=1\n", encoding="utf-8")
    (root / "workspace").mkdir(exist_ok=True)
    (root / "workspace" / "runs.txt").write_text("should not be packaged\n", encoding="utf-8")
    (root / "BUILD_STATUS.md").write_text("# status\n", encoding="utf-8")
    for rel, text in (extra_files or {}).items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return root


def build_fake_release(
    tmp: Path,
    *,
    version: str = "1.0.0",
    profile: Any = None,
    selftest_ok: bool = True,
    with_deps: bool = True,
    db_schema_version: int | None = None,
    extra_files: dict[str, str] | None = None,
    label: str = "",
) -> Any:
    """가짜 프로젝트/wheelhouse/앱 wheel 로 release ZIP 을 만든다. ReleaseBuildResult 반환."""
    from corp_dl_agent.packaging.release import build_release_detailed

    profile = profile or host_profile()
    tag = f"{version}{label}"
    project = make_fake_project(tmp / f"proj-{tag}", extra_files=extra_files)
    wheelhouse = make_fake_wheelhouse(tmp / f"wh-{tag}" / profile.profile_id, profile, with_binary=with_deps)
    requires = ("fake-dep", "fake-bin") if with_deps else ("fake-dep",)
    app = make_fake_app_wheel(tmp / f"app-{tag}", version, selftest_ok=selftest_ok, requires=requires)
    return build_release_detailed(
        project,
        profile=profile,
        wheelhouse_dir=wheelhouse,
        app_wheel=app,
        out_dir=tmp / f"dist-{tag}",
        evidence_dir=project / "test-evidence",
        app_module=FAKE_MODULE,
        db_schema_version=db_schema_version,
    )


def script_env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("DIA_") and k != "PYTHON"}
    env["PYTHONIOENCODING"] = "utf-8"
    env.update(extra)
    return env


def run_script(
    name: str, *args: str, cwd: Path | None = None, env: dict[str, str] | None = None, timeout: int = 300
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / name), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(cwd) if cwd else None,
        env=env or script_env(),
        timeout=timeout,
    )


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def test_make_wheel_is_valid_zip_with_record(tmp_path: Path) -> None:
    w = make_fake_app_wheel(tmp_path, "9.9.9")
    with zipfile.ZipFile(w) as zf:
        names = zf.namelist()
        assert f"{FAKE_MODULE}/__main__.py" in names
        record = zf.read("fake_app-9.9.9.dist-info/RECORD").decode()
        assert record.strip().endswith("fake_app-9.9.9.dist-info/RECORD,,")
        meta = zf.read("fake_app-9.9.9.dist-info/METADATA").decode()
        assert "Name: fake-app" in meta and "License-Expression: MIT" in meta
        assert zf.testzip() is None
