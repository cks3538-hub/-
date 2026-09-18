"""scripts/ 규칙 시험: 표준 라이브러리만 import(ast), _common ≡ verifier_core 동기화, wrapper(.cmd/.ps1/.sh) 규칙,
preflight 의 target_profile.min.json 허용 필드, pip 환경 차단, 오래된 Python 진단."""

from __future__ import annotations

import ast
import getpass
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from corp_dl_agent.packaging import verifier_core as core
from tests.test_packaging_kit import ROOT, SCRIPTS_DIR, read_json, run_script, script_env

# scripts 끼리의 import (같은 폴더의 표준 라이브러리 전용 모듈)
LOCAL_SCRIPT_MODULES = frozenset({"_common", "launch"})
CMD_TO_SCRIPT = {
    "00_Preflight.cmd": "preflight.py",
    "01_Verify.cmd": "verify.py",
    "02_Install.cmd": "install.py",
    "03_Start.cmd": "launch.py",
    "04_SelfTest.cmd": "selftest.py",
    "05_Update.cmd": "upgrade.py",
    "06_Rollback.cmd": "rollback.py",
    "launch.cmd": "launch.py",
}
AUTO_INSTALL_MARKERS = (
    "winget",
    "msstore",
    "ms-windows-store",
    "Invoke-WebRequest",
    "python.org/ftp",
    "curl ",
    "wget ",
)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_scripts_import_stdlib_only() -> None:
    stdlib = set(sys.stdlib_module_names)
    scripts = sorted(SCRIPTS_DIR.glob("*.py"))
    assert {p.name for p in scripts} >= {
        "_common.py",
        "preflight.py",
        "verify.py",
        "install.py",
        "launch.py",
        "selftest.py",
        "upgrade.py",
        "rollback.py",
        "acceptance.py",
    }
    for p in scripts:
        names = _imports(p)
        bad = names - stdlib - LOCAL_SCRIPT_MODULES
        assert not bad, f"{p.name}: 표준 라이브러리 밖 import {sorted(bad)}"
        assert "corp_dl_agent" not in names, (
            f"{p.name}: 앱 미설치 상태에서 실행되므로 corp_dl_agent 를 import 하면 안 됨"
        )


def test_common_is_byte_identical_to_verifier_core() -> None:
    a = (SCRIPTS_DIR / "_common.py").read_bytes()
    b = (ROOT / "corp_dl_agent" / "packaging" / "verifier_core.py").read_bytes()
    assert a == b, "scripts/_common.py 와 corp_dl_agent/packaging/verifier_core.py 가 다릅니다 (cp 로 동기화)"


def _uses_datetime_utc(tree: ast.AST) -> bool:
    """3.11 전용 datetime.UTC 사용 여부 (docstring 언급은 제외, 코드만)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "datetime":
            if any(a.name == "UTC" for a in node.names):
                return True
        if isinstance(node, ast.Attribute) and node.attr == "UTC":
            if isinstance(node.value, ast.Name) and node.value.id == "datetime":
                return True
    return False


def test_scripts_have_main_guard_and_no_311_only_api() -> None:
    for p in sorted(SCRIPTS_DIR.glob("*.py")):
        text = p.read_text(encoding="utf-8")
        if p.name != "_common.py":
            assert 'if __name__ == "__main__":' in text, p.name
        assert not _uses_datetime_utc(ast.parse(text)), (
            f"{p.name}: datetime.UTC 는 3.11 전용 — 오래된 Python 에서도 진단이 나오도록 timezone.utc 를 사용"
        )


def test_scripts_help_exits_zero() -> None:
    for name in (
        "preflight.py",
        "verify.py",
        "install.py",
        "upgrade.py",
        "rollback.py",
        "acceptance.py",
        "launch.py",
    ):
        r = run_script(name, "--help", timeout=60)
        assert r.returncode == 0, f"{name} --help: {r.stderr[-300:]}"
        assert "사용" in r.stdout or "usage" in r.stdout.lower()


def test_cmd_wrappers_follow_rules() -> None:
    for cmd, script in CMD_TO_SCRIPT.items():
        text = (SCRIPTS_DIR / cmd).read_text(encoding="utf-8", errors="replace")
        assert "%~dp0" in text, f"{cmd}: 스크립트 위치 기준(%~dp0) 동작이 없습니다"
        assert "if defined PYTHON" in text, f"{cmd}: 지정 Python 경로(PYTHON) 지원이 없습니다"
        assert f'"%SCRIPT_DIR%{script}"' in text, f"{cmd}: {script} 경로가 따옴표로 전달되지 않습니다"
        assert '--install-root "%DIA_INSTALL_ROOT%"' in text, (
            f"{cmd}: install root 인자가 따옴표로 전달되지 않습니다"
        )
        assert "%*" in text, f"{cmd}: 추가 인자 전달(%*) 이 없습니다"
        assert "exit /b %EC%" in text, f"{cmd}: 종료 코드를 반환하지 않습니다"
        assert "WindowsApps" in text, f"{cmd}: Store alias python 제외 처리가 없습니다"
        assert "EC=8" in text, f"{cmd}: Python 없음 → exit 8 (BLOCKED_PREREQUISITE) 처리가 없습니다"
        for marker in AUTO_INSTALL_MARKERS:
            assert marker not in text, f"{cmd}: 자동 설치/다운로드 흔적 '{marker}'"


def test_ps1_and_sh_wrappers_follow_rules() -> None:
    ps1 = (SCRIPTS_DIR / "preflight.ps1").read_text(encoding="utf-8-sig", errors="replace")
    assert "Set-ExecutionPolicy" not in ps1 and "Unblock-File" not in ps1
    for marker in AUTO_INSTALL_MARKERS:
        assert marker not in ps1, f"preflight.ps1: 자동 설치 흔적 '{marker}'"
    assert "exit 8" in ps1 and "ensurepip" in ps1 and "$env:PYTHON" in ps1
    for sh in ("install.sh", "launch.sh", "selftest.sh"):
        text = (SCRIPTS_DIR / sh).read_text(encoding="utf-8")
        assert 'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"' in text, sh
        assert "exit 8" in text and 'PY="${PYTHON:-}"' in text, sh
        assert 'exec "$PY"' in text and '"$@"' in text, sh
        for marker in ("apt-get", "pip install", "curl ", "wget "):
            assert marker not in text, f"{sh}: '{marker}'"


def test_sanitized_env_blocks_pip_and_proxy_settings(tmp_path: Path) -> None:
    base = {
        "PATH": os.environ.get("PATH", ""),
        "PIP_INDEX_URL": "http://127.0.0.1:9/simple",
        "PIP_EXTRA_INDEX_URL": "http://127.0.0.1:9/extra",
        "PIP_FIND_LINKS": str(tmp_path),
        "PIP_CONFIG_FILE": str(tmp_path / "pip.conf"),
        "HTTP_PROXY": "http://127.0.0.1:9",
        "https_proxy": "http://127.0.0.1:9",
        "PYTHONPATH": str(tmp_path),
        "PYTHONHOME": str(tmp_path),
        "VIRTUAL_ENV": str(tmp_path),
        "REQUESTS_CA_BUNDLE": str(tmp_path / "ca.pem"),
        "DIA_INSTALL_ROOT": str(tmp_path),
        "HOME": str(tmp_path),
    }
    env = core.sanitized_env(base)
    for k in (
        "PIP_INDEX_URL",
        "PIP_EXTRA_INDEX_URL",
        "PIP_FIND_LINKS",
        "HTTP_PROXY",
        "https_proxy",
        "PYTHONPATH",
        "PYTHONHOME",
        "VIRTUAL_ENV",
        "REQUESTS_CA_BUNDLE",
    ):
        assert k not in env, k
    assert env["PIP_CONFIG_FILE"] == os.devnull
    assert env["PIP_NO_INPUT"] == "1" and env["PYTHONNOUSERSITE"] == "1"
    assert env["DIA_INSTALL_ROOT"] == str(tmp_path) and env["HOME"] == str(tmp_path)
    cmd = core.pip_install_command(Path("py"), Path("wh"), Path("app"), Path("lock.txt"))
    for flag in (
        "--isolated",
        "--no-index",
        "--require-hashes",
        "--only-binary=:all:",
        "--no-cache-dir",
        "--disable-pip-version-check",
        "--no-input",
    ):
        assert flag in cmd, flag
    assert "--index-url" not in cmd and "--extra-index-url" not in cmd


def test_min_profile_has_only_allowed_fields_and_redacts_paths() -> None:
    host = core.detect_host()
    host["os_version"] = "C:\\" + "Users\\someone\\weird"  # 경로 형태 값은 REDACTED 되어야 함
    minimal = core.min_profile(host, preflight_status="PASS", feature_status={"core": "PASS"})
    assert set(minimal) <= set(core.MIN_PROFILE_FIELDS)
    for forbidden in (
        "verified_environment",
        "notes",
        "python_executable",
        "hostname",
        "user",
        "install_root",
        "files",
    ):
        assert forbidden not in minimal
    assert minimal["os_version"] == "REDACTED"
    assert minimal["format"] == core.MIN_PROFILE_FORMAT and minimal["preflight_status"] == "PASS"
    assert minimal["feature_status"] == {"core": "PASS"}
    text = json.dumps(minimal, ensure_ascii=False)
    assert "://" not in text and platform.node() not in text


def test_preflight_script_writes_min_profile_without_forbidden_fields(tmp_path: Path) -> None:
    install_root = tmp_path / "설치 root 한글 공백" / "DIA"
    report = tmp_path / "preflight_report.json"
    min_out = tmp_path / "target_profile.min.json"
    r = run_script(
        "preflight.py",
        "--install-root",
        str(install_root),
        "--report",
        str(report),
        "--min-out",
        str(min_out),
        "--required-free-gb",
        "0.05",
        "--json",
        cwd=tmp_path,
        timeout=120,
    )
    assert r.returncode == 0, r.stderr[-500:] + r.stdout[-500:]
    out = json.loads(r.stdout)
    assert out["status"] in ("PASS", "PARTIAL")
    names = {c["name"]: c["status"] for c in out["checks"]}
    assert (
        names["python_present"] == "PASS"
        and names["venv_module"] == "PASS"
        and names["ensurepip_module"] == "PASS"
    )
    assert names["korean_space_path"] == "PASS" and names["write_permission_install_root"] == "PASS"
    minimal = read_json(min_out)
    assert set(minimal) <= set(core.MIN_PROFILE_FIELDS)
    text = min_out.read_text(encoding="utf-8")
    for forbidden in (
        str(tmp_path),
        str(Path.home()),
        platform.node(),
        sys.executable,
        getpass.getuser(),
        "://",
    ):
        if forbidden:
            assert forbidden not in text, f"target_profile.min.json 에 금지 값 포함: {forbidden}"
    detail = read_json(report)
    assert detail["install_root"] == str(install_root)  # 상세 보고서(로컬) 에만 경로가 있다
    assert not install_root.exists() or not any(install_root.iterdir()), (
        "preflight 는 설치 폴더를 만들지 않는다"
    )


def test_preflight_missing_python_is_blocked_prerequisite(tmp_path: Path) -> None:
    r = run_script(
        "preflight.py",
        "--python",
        str(tmp_path / "없는 python.exe"),
        "--install-root",
        str(tmp_path / "ir"),
        "--report",
        str(tmp_path / "r.json"),
        "--min-out",
        str(tmp_path / "m.json"),
        timeout=60,
    )
    assert r.returncode == core.EXIT_BLOCKED_PREREQUISITE
    assert "BLOCKED_PREREQUISITE" in r.stdout and "자동 설치하지 않" in r.stdout


@pytest.mark.skipif(shutil.which("python3.10") is None, reason="python3.10 이 호스트에 없음")
def test_preflight_on_old_python_reports_version_not_crash(tmp_path: Path) -> None:
    """오래된 Python 으로 실행해도 ImportError 로 죽지 않고 '버전이 낮다' 진단(exit 8) 이 나와야 한다."""
    old = shutil.which("python3.10")
    assert old is not None
    r = subprocess.run(
        [
            old,
            str(SCRIPTS_DIR / "preflight.py"),
            "--python",
            old,
            "--install-root",
            str(tmp_path / "ir"),
            "--report",
            str(tmp_path / "r.json"),
            "--min-out",
            str(tmp_path / "m.json"),
            "--json",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=script_env(),
        timeout=120,
    )
    assert r.returncode == core.EXIT_BLOCKED_PREREQUISITE, r.stderr[-800:]
    out = json.loads(r.stdout)
    names = {c["name"]: c["status"] for c in out["checks"]}
    assert names["python_version"] == "FAIL" and "python_version" in out["blocked"]


def test_cmd_wrappers_are_ascii_crlf_without_chcp() -> None:
    """cmd.exe 는 UTF-8 한글이 든 배치 파일(특히 chcp 65001 이후)을 잘못 파싱한다 — 실제 Windows 에서 재현된 결함.
    래퍼는 ASCII + CRLF 만 허용하고 chcp 를 쓰지 않는다. 한국어 메시지는 Python 스크립트가 출력한다."""
    root = SCRIPTS_DIR.parent
    for path in [*sorted(SCRIPTS_DIR.glob("*.cmd")), root / "tools" / "Rebuild_Release_Windows.cmd"]:
        raw = path.read_bytes()
        assert all(b < 0x80 for b in raw), f"{path.name}: non-ASCII bytes present"
        assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b""), f"{path.name}: CRLF 가 아닌 줄바꿈"
        assert b"chcp" not in raw.lower(), f"{path.name}: chcp 사용 금지"
    ps1 = (SCRIPTS_DIR / "preflight.ps1").read_bytes()
    assert ps1.startswith(b"\xef\xbb\xbf"), (
        "preflight.ps1 은 UTF-8 BOM 이어야 PowerShell 5.1 이 한글을 바르게 읽는다"
    )


def _cmd_block_lint(path: Path) -> list[str]:
    """cmd.exe 는 ( ... ) 블록을 통째로 파싱하므로, 블록 안 echo 줄의 따옴표 밖 괄호는 블록을 조기 종료시킨다
    (실제 Windows 에서 'and은(는) 예상되지 않았습니다' 로 재현된 결함)."""
    import re

    problems: list[str] = []
    depth = 0
    for lineno, line in enumerate(path.read_bytes().decode("ascii").split("\r\n"), 1):
        s = line.strip()
        if s.lower().startswith("rem "):
            if depth > 0 and ("(" in s or ")" in s):
                problems.append(f"{path.name}:{lineno}: rem 에 괄호 (블록 안)")
            continue
        unq = re.sub(r'"[^"]*"', '""', s)
        unq = re.sub(r"\^.", "", unq)
        if re.fullmatch(r"\)\s*else\s*\(", unq):  # 블록 연결 구문은 깊이 변화 없음
            continue
        if depth > 0:
            body = unq[:-1] if unq.endswith("(") else unq
            if body == ")":
                body = ""
            if body.count("(") != body.count(")"):
                problems.append(f"{path.name}:{lineno}: 블록 안 괄호 불균형: {s[:80]}")
            elif ("(" in body or ")" in body) and s.lower().startswith("echo"):
                problems.append(f"{path.name}:{lineno}: 블록 안 echo 에 괄호: {s[:80]}")
        depth = max(depth + unq.count("(") - unq.count(")"), 0)
    return problems


def test_cmd_wrappers_no_parentheses_in_echo_inside_blocks() -> None:
    root = SCRIPTS_DIR.parent
    problems: list[str] = []
    for path in [*sorted(SCRIPTS_DIR.glob("*.cmd")), root / "tools" / "Rebuild_Release_Windows.cmd"]:
        problems += _cmd_block_lint(path)
    assert not problems, "\n".join(problems)
