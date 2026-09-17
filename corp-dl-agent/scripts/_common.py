"""corp-dl-agent 반입 패키지 검증·설치·업데이트·롤백 공통 코어 (표준 라이브러리만).

- `scripts/_common.py` 와 `corp_dl_agent/packaging/verifier_core.py` 는 byte 단위로 동일해야 한다
  (tests/test_scripts_stdlib.py 가 검사). 앱 쪽 packaging/commands 모듈과 설치 스크립트가 같은
  파서/검사기/설치기를 쓰도록 하기 위한 구조이며, 스크립트는 앱이 설치되지 않은 상태(사내 PC 첫 실행)에서
  동작해야 하므로 corp_dl_agent 를 import 하지 않는다.
- 구성: 상수/유틸 → wheel·tag → lock → checksums → 호스트/타깃 프로파일 → archive(PackageSource) → manifest 검사 →
  verify_package → 설치 root/active.json → sqlite 도우미 → install → upgrade(backup/migration) → rollback.
- 구문/런타임은 오래된 Python(3.9+) 에서도 import 되어 "버전이 낮다" 는 진단이 나오도록 유지한다
  (3.11 전용 API 사용 금지: datetime.UTC 등).
- 비밀값(토큰/키)은 어떤 보고서에도 기록하지 않는다. 경로는 로컬 상세 보고서에만 기록한다.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import sqlite3
import stat
import struct
import subprocess
import sys
import tempfile
import time
import zipfile
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# ----------------------------------------------------------------------------- 상수
MANIFEST_NAME = "release-manifest.json"
CHECKSUMS_NAME = "checksums.sha256"
INVENTORY_NAME = "dependency-inventory.json"
ACTIVE_NAME = "active.json"
INSTALL_LOG_NAME = "install_log.json"
MANIFEST_FORMAT = "dia-release-manifest/1"
ACCEPTANCE_FORMAT = "dia-acceptance/1"
MIN_PROFILE_FORMAT = "dia-target-profile-min/1"

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_BLOCKED_CONFIG = 3
EXIT_BLOCKED_DEPENDENCY = 4
EXIT_VALIDATION = 5
EXIT_RUNTIME = 6
EXIT_BUDGET = 7
EXIT_BLOCKED_PREREQUISITE = 8
EXIT_NOT_SUPPORTED = 9

EXIT_BY_CODE = {
    "E_USAGE": EXIT_USAGE,
    "E_BLOCKED_CONFIG": EXIT_BLOCKED_CONFIG,
    "E_BLOCKED_DEPENDENCY": EXIT_BLOCKED_DEPENDENCY,
    "E_BLOCKED_PREREQUISITE": EXIT_BLOCKED_PREREQUISITE,
    "E_PACKAGE_INVALID": EXIT_VALIDATION,
    "E_INSTALL_FAILED": EXIT_RUNTIME,
    "E_UPGRADE_LOCKED": EXIT_RUNTIME,
    "E_ROLLBACK_INCOMPATIBLE": EXIT_RUNTIME,
    "E_NOT_SUPPORTED": EXIT_NOT_SUPPORTED,
    "E_INTERNAL": EXIT_RUNTIME,
}

PACKAGE_KINDS = ("SOURCE_ONLY", "CPU_OFFLINE", "GPU_OFFLINE")
VERIFICATION_KEYS = (
    "HOST_CORE_TESTED",
    "TARGET_BUNDLE_PREPARED",
    "TARGET_OFFLINE_TESTED",
    "CORP_INSTALLED",
    "CORP_INTEGRATED",
    "BUSINESS_VALIDATED",
)
CORP_ONLY_KEYS = ("CORP_INSTALLED", "CORP_INTEGRATED", "BUSINESS_VALIDATED")
STATUS_VALUES = (
    "PASS",
    "FAIL",
    "NOT_RUN",
    "BLOCKED",
    "PARTIAL",
    "MOCK_TESTED",
    "SOURCE_ONLY",
    "TARGET_UNCONFIRMED",
)

RELEASE_ID_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[.\-+][A-Za-z0-9.\-]{1,40})?$")
PROFILE_ID_RE = re.compile(r"^(win|linux|macos)-(x64|arm64)-cp3[0-9]{1,2}-(cpu|gpu)$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# target_profile.min.json 에 허용되는 필드 (이름/호스트/사용자 경로/URL/파일 목록/인증값 제외)
MIN_PROFILE_FIELDS = (
    "profile_id",
    "os",
    "os_version",
    "architecture",
    "python_implementation",
    "python_version",
    "python_abi",
    "platform_tags",
    "features",
    "feature_status",
    "device",
    "gpu",
    "bits",
    "target_confirmed",
    "preflight_status",
    "generated_at",
    "format",
)
DEFAULT_FEATURES = ["core", "documents", "ml-cpu"]

WORKSPACE_SUBDIRS = ("data", "runs", "models", "outputs", "state", "indexes", "logs")
COMPANY_SUBDIRS = ("config", "templates", "extensions")
STATE_DB_RELATIVE = os.path.join("state", "agent_state.sqlite")


class ScriptError(Exception):
    """스크립트 사용자 오류. code 는 corp_dl_agent.errors.ERROR_CATALOG 의 키와 같은 이름을 쓴다."""

    def __init__(
        self, code: str, message: str, *, hint: str = "", details: Optional[dict[str, Any]] = None
    ) -> None:
        self.code = code
        self.message = message
        self.hint = hint
        self.details = details or {}
        self.exit_code = EXIT_BY_CODE.get(code, EXIT_RUNTIME)
        super().__init__(f"[{code}] {message}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "hint": self.hint,
            "details": self.details,
            "exit_code": self.exit_code,
        }

    def format_ko(self) -> str:
        lines = [f"오류 {self.code}: {self.message}"]
        if self.hint:
            lines.append(f"해결 방법: {self.hint}")
        for k, v in self.details.items():
            if isinstance(v, list):
                lines.append(f"  - {k}:")
                for item in v[:30]:
                    lines.append(f"      {item}")
                if len(v) > 30:
                    lines.append(f"      ... ({len(v) - 30}개 더)")
            else:
                lines.append(f"  - {k}: {v}")
        return "\n".join(lines)


# ----------------------------------------------------------------------------- 기본 유틸
def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()  # noqa: UP017 - 3.9+ import 유지


def timestamp_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")  # noqa: UP017 - 3.9+ import 유지


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | os.PathLike[str], chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def sha256_stream(fobj: Any, chunk: int = 1 << 20) -> tuple[str, int]:
    h = hashlib.sha256()
    n = 0
    while True:
        b = fobj.read(chunk)
        if not b:
            break
        n += len(b)
        h.update(b)
    return h.hexdigest(), n


def read_json(path: str | os.PathLike[str]) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def dump_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n"


def write_bytes_atomic(path: str | os.PathLike[str], data: bytes) -> None:
    """temp -> flush+fsync -> os.replace. 같은 폴더에 temp 를 만든다 (Windows 호환)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{p.name}.", suffix=".tmp", dir=str(p.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        try:
            umask = os.umask(0)
            os.umask(umask)
            os.chmod(tmp, 0o666 & ~umask)
        except OSError:
            pass
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_text_atomic(path: str | os.PathLike[str], text: str) -> None:
    write_bytes_atomic(path, text.encode("utf-8"))


def write_json_atomic(path: str | os.PathLike[str], obj: Any) -> None:
    write_text_atomic(path, dump_json(obj))


def tail(text: str, n: int = 40) -> str:
    lines = text.splitlines()
    return "\n".join(lines[-n:])


def run_logged(
    cmd: list[str],
    *,
    env: Optional[dict[str, str]] = None,
    cwd: Optional[str] = None,
    timeout: int = 3600,
    tail_lines: int = 40,
) -> dict[str, Any]:
    """subprocess 실행 결과를 기록용 dict 로 반환한다 (stdout/stderr 꼬리만 보관)."""
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            cwd=cwd,
            timeout=timeout,
        )
        code, out, err, timed_out = proc.returncode, proc.stdout, proc.stderr, False
    except subprocess.TimeoutExpired as exc:
        code, timed_out = -1, True
        out = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode("utf-8", "replace")
        err = exc.stderr if isinstance(exc.stderr, str) else (exc.stderr or b"").decode("utf-8", "replace")
    except OSError as exc:
        code, out, err, timed_out = -2, "", f"{type(exc).__name__}: {exc}", False
    return {
        "command": cmd,
        "exit_code": code,
        "timed_out": timed_out,
        "elapsed_seconds": round(time.monotonic() - t0, 2),
        "stdout_tail": tail(out, tail_lines),
        "stderr_tail": tail(err, tail_lines),
    }


def sanitized_env(base: Optional[dict[str, str]] = None) -> dict[str, str]:
    """pip/프록시/Python 사용자 설정이 설치 subprocess 에 상속되지 않도록 차단한 환경."""
    src = dict(os.environ if base is None else base)
    env: dict[str, str] = {}
    for k, v in src.items():
        ku = k.upper()
        if ku.startswith("PIP_") or ku.startswith("PYTHON"):
            continue
        if ku.endswith("_PROXY"):
            continue
        if ku in (
            "REQUESTS_CA_BUNDLE",
            "CURL_CA_BUNDLE",
            "SSL_CERT_FILE",
            "SSL_CERT_DIR",
            "VIRTUAL_ENV",
            "CONDA_PREFIX",
        ):
            continue
        env[k] = v
    env["PIP_CONFIG_FILE"] = os.devnull
    env["PIP_NO_INPUT"] = "1"
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PYTHONNOUSERSITE"] = "1"
    return env


def disk_free_bytes(path: str | os.PathLike[str]) -> tuple[int, str]:
    """path 또는 존재하는 가장 가까운 상위 폴더의 여유 공간."""
    p = Path(path).expanduser()
    probe = p
    while not probe.exists():
        if probe.parent == probe:
            break
        probe = probe.parent
    try:
        return shutil.disk_usage(str(probe)).free, str(probe)
    except OSError:
        return -1, str(probe)


def nearest_existing(path: Path) -> Path:
    p = path
    while not p.exists():
        if p.parent == p:
            break
        p = p.parent
    return p


def writable_dir(path: Path) -> tuple[bool, str]:
    """폴더(또는 가장 가까운 상위)에 임시 파일을 만들어 쓰기 권한을 확인한다. 폴더를 새로 만들지 않는다."""
    base = nearest_existing(path)
    try:
        fd, tmp = tempfile.mkstemp(prefix=".dia-write-test-", dir=str(base))
        os.close(fd)
        os.unlink(tmp)
        return True, str(base)
    except OSError as exc:
        return False, f"{base}: {exc}"


def korean_space_path_ok(base: Path) -> tuple[bool, str]:
    """한글/공백 경로 생성·쓰기·읽기 가능 여부."""
    root = nearest_existing(base)
    name = f"dia 한글 공백 검사 {os.getpid()}"
    d = root / name
    try:
        d.mkdir(exist_ok=True)
        f = d / "확인 파일.txt"
        f.write_text("ok", encoding="utf-8")
        ok = f.read_text(encoding="utf-8") == "ok"
        f.unlink()
        d.rmdir()
        return ok, str(root)
    except OSError as exc:
        try:
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass
        return False, f"{root}: {exc}"


# ----------------------------------------------------------------------------- 이름/태그/wheel
def normalize_name(name: str) -> str:
    """PEP 503 정규화: 소문자, [-_.]+ -> '-'."""
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_wheel_filename(filename: str) -> dict[str, Any]:
    """wheel 파일명 -> name/version/build/python_tags/abi_tags/platform_tags. 잘못된 이름은 ScriptError."""
    base = os.path.basename(filename)
    if not base.endswith(".whl"):
        raise ScriptError("E_PACKAGE_INVALID", f"wheel 파일명이 아닙니다: {base}")
    stem = base[:-4]
    parts = stem.split("-")
    if len(parts) == 5:
        name, version, build, py, abi, plat = parts[0], parts[1], None, parts[2], parts[3], parts[4]
    elif len(parts) == 6:
        name, version, build, py, abi, plat = parts
    else:
        raise ScriptError("E_PACKAGE_INVALID", f"wheel 파일명 형식이 올바르지 않습니다: {base}")
    if not name or not version or not py or not abi or not plat:
        raise ScriptError("E_PACKAGE_INVALID", f"wheel 파일명 구성 요소가 비어 있습니다: {base}")
    return {
        "filename": base,
        "name": normalize_name(name),
        "raw_name": name,
        "version": version,
        "build": build,
        "python_tags": py.split("."),
        "abi_tags": abi.split("."),
        "platform_tags": plat.split("."),
    }


_MANYLINUX_LEGACY = {"manylinux1": (2, 5), "manylinux2010": (2, 12), "manylinux2014": (2, 17)}


def _parse_platform_tag(tag: str) -> tuple[str, Optional[tuple[int, int]], str]:
    """platform tag -> (family, version, arch). family: any|win|manylinux|linux|macosx|other."""
    if tag == "any":
        return "any", None, ""
    if tag.startswith("win"):
        return "win", None, tag[len("win_") :] if tag.startswith("win_") else tag[3:]
    m = re.match(r"^manylinux_(\d+)_(\d+)_(.+)$", tag)
    if m:
        return "manylinux", (int(m.group(1)), int(m.group(2))), m.group(3)
    m = re.match(r"^(manylinux1|manylinux2010|manylinux2014)_(.+)$", tag)
    if m:
        return "manylinux", _MANYLINUX_LEGACY[m.group(1)], m.group(2)
    m = re.match(r"^linux_(.+)$", tag)
    if m:
        return "linux", None, m.group(1)
    m = re.match(r"^macosx_(\d+)_(\d+)_(.+)$", tag)
    if m:
        return "macosx", (int(m.group(1)), int(m.group(2))), m.group(3)
    return "other", None, tag


def platform_tag_compatible(wheel_tag: str, target_tag: str) -> bool:
    """wheel 의 platform tag 하나가 target 이 허용하는 tag 하나와 호환되는가.

    target_tag 는 대상이 지원하는 상한(manylinux_X_Y = glibc X.Y 이하 허용, macosx_X_Y = 그 이하 허용)을 뜻한다.
    """
    if wheel_tag == "any":
        return True
    wf, wv, wa = _parse_platform_tag(wheel_tag)
    tf, tv, ta = _parse_platform_tag(target_tag)
    if tf == "any":
        return False
    if wf == "win":
        return tf == "win" and wa == ta
    if wf == "manylinux":
        if tf != "manylinux" or wa != ta:
            return False
        return wv is not None and tv is not None and wv <= tv
    if wf == "linux":
        return tf == "linux" and wa == ta
    if wf == "macosx":
        if tf != "macosx" or wv is None or tv is None or wv > tv:
            return False
        return wa == ta or wa == "universal2" and ta in ("arm64", "x86_64")
    return wheel_tag == target_tag


def python_tag_compatible(python_tags: list[str], abi_tags: list[str], target_abi: str) -> bool:
    """python tag (py3/cp312/...) 와 대상 ABI (cp312) 호환."""
    m = re.match(r"^([a-z]{2})(\d)(\d+)$", target_abi)
    if not m:
        return False
    impl, major, minor = m.group(1), int(m.group(2)), int(m.group(3))
    abi3 = "abi3" in abi_tags
    for t in python_tags:
        if t == f"py{major}" or t == target_abi or t == f"{impl}{major}":
            return True
        m2 = re.match(r"^py(\d)(\d+)$", t)
        if m2 and int(m2.group(1)) == major and int(m2.group(2)) == minor:
            return True
        m3 = re.match(r"^([a-z]{2})(\d)(\d+)$", t)
        if m3 and abi3 and m3.group(1) == impl and int(m3.group(2)) == major and int(m3.group(3)) <= minor:
            return True
    return False


def abi_tag_compatible(abi_tags: list[str], target_abi: str) -> bool:
    return any(t in ("none", "abi3", target_abi) for t in abi_tags)


def wheel_compatibility_problems(info: dict[str, Any], target: dict[str, Any]) -> list[str]:
    """wheel 이 target(python_abi, platform_tags) 에서 설치 가능한지. 문제 목록(비면 호환)."""
    problems: list[str] = []
    abi = str(target.get("python_abi", ""))
    plats = [str(p) for p in target.get("platform_tags", [])]
    if not python_tag_compatible(info["python_tags"], info["abi_tags"], abi):
        problems.append(
            "{}: python tag {} 는 {} 와 호환되지 않음".format(
                info["filename"], ".".join(info["python_tags"]), abi
            )
        )
    if not abi_tag_compatible(info["abi_tags"], abi):
        problems.append(
            "{}: abi tag {} 는 {} 와 호환되지 않음".format(info["filename"], ".".join(info["abi_tags"]), abi)
        )
    if not any(platform_tag_compatible(w, t) for w in info["platform_tags"] for t in plats):
        problems.append(
            "{}: platform tag {} 는 대상 {} 와 호환되지 않음".format(
                info["filename"], ".".join(info["platform_tags"]), plats
            )
        )
    return problems


# ----------------------------------------------------------------------------- lock (pip --require-hashes)
LOCK_LINE_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)==(?P<version>[A-Za-z0-9.!+_-]+)(?P<hashes>(?:\s+--hash=sha256:[0-9a-f]{64})+)$"
)
_LOCK_FORBIDDEN = (
    (
        re.compile(r"^-"),
        "옵션/지시문 (-e, -r, -c, -i, -f, --index-url, --extra-index-url, --find-links, --trusted-host 등)",
    ),
    (re.compile(r"://"), "URL"),
    (re.compile(r"(?i)^(git|hg|svn|bzr)\+"), "VCS 참조"),
    (re.compile(r"@"), "direct reference (name @ url/경로)"),
    (re.compile(r"^(\.|/|\\|~|[A-Za-z]:[\\/])"), "디렉터리/파일 경로"),
    (re.compile(r"\$\{|\$[A-Za-z_]"), "환경 변수 확장"),
    (re.compile(r";"), "환경 marker (프로파일별 lock 은 marker 를 쓰지 않음)"),
    (re.compile(r"\["), "extras"),
    (re.compile(r"(?i)^file:"), "file: 참조"),
)


def parse_lock_text(text: str) -> list[dict[str, Any]]:
    """lock 텍스트를 검증하며 파싱. 허용 형식: 'name==version --hash=sha256:<64hex>' (+ 줄 이어쓰기 '\\'). 위반 시 ScriptError."""
    logical: list[tuple[int, str]] = []
    buf = ""
    buf_line = 0
    for i, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip()
        if line.lstrip().startswith("#"):
            if buf:
                raise ScriptError("E_PACKAGE_INVALID", f"lock {i}행: 이어쓰기 중 주석")
            continue
        # 줄 끝 주석 제거 (공백 뒤 '#')
        m = re.search(r"\s#", line)
        if m:
            line = line[: m.start()].rstrip()
        if line.endswith("\\"):
            buf += line[:-1].rstrip() + " "
            if not buf_line:
                buf_line = i
            continue
        if buf:
            line = buf + line
            buf = ""
            i = buf_line
            buf_line = 0
        if not line.strip():
            continue
        logical.append((i, line.strip()))
    if buf:
        raise ScriptError("E_PACKAGE_INVALID", "lock 마지막 줄이 '\\' 로 끝납니다")
    entries: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    for lineno, line in logical:
        for pat, why in _LOCK_FORBIDDEN:
            if pat.search(line):
                raise ScriptError(
                    "E_PACKAGE_INVALID",
                    f"lock {lineno}행: 허용되지 않는 항목 ({why})",
                    hint="lock 은 'name==version --hash=sha256:...' 형식만 허용합니다. URL/VCS/editable/경로/index 옵션은 거부됩니다.",
                    details={"line": line[:120]},
                )
        m = LOCK_LINE_RE.match(line)
        if not m:
            raise ScriptError(
                "E_PACKAGE_INVALID",
                f"lock {lineno}행: 형식 오류 (version pin 과 sha256 hash 가 필요)",
                details={"line": line[:120]},
            )
        name = normalize_name(m.group("name"))
        hashes = re.findall(r"--hash=sha256:([0-9a-f]{64})", m.group("hashes"))
        if name in seen:
            raise ScriptError("E_PACKAGE_INVALID", f"lock {lineno}행: 중복 패키지 {name} ({seen[name]}행)")
        seen[name] = lineno
        entries.append(
            {"name": name, "version": m.group("version"), "hashes": sorted(set(hashes)), "line": lineno}
        )
    if not entries:
        raise ScriptError("E_PACKAGE_INVALID", "lock 에 항목이 없습니다")
    return entries


def format_lock_text(entries: Iterable[dict[str, Any]], header_lines: Iterable[str] = ()) -> str:
    lines = [f"# {h}" for h in header_lines]
    for e in sorted(entries, key=lambda x: (x["name"], x["version"])):
        hashes = " ".join(f"--hash=sha256:{h}" for h in sorted(e["hashes"]))
        lines.append("{}=={} {}".format(e["name"], e["version"], hashes))
    return "\n".join(lines) + "\n"


def cross_check_lock(entries: list[dict[str, Any]], wheels: list[dict[str, Any]]) -> list[str]:
    """lock 항목과 wheel 목록(name/version/sha256/filename)의 양방향 일치. 문제 목록."""
    problems: list[str] = []
    by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for w in wheels:
        by_key.setdefault((w["name"], w["version"]), []).append(w)
    for _key, ws in by_key.items():
        if len(ws) > 1:
            problems.append(
                "동일 패키지의 wheel 이 여러 개입니다: {}".format(", ".join(w["filename"] for w in ws))
            )
    lock_keys = {(e["name"], e["version"]): e for e in entries}
    for e in entries:
        ws = by_key.get((e["name"], e["version"]), [])
        if not ws:
            problems.append(
                "lock 항목 {}=={} 에 해당하는 wheel 이 wheelhouse 에 없습니다".format(e["name"], e["version"])
            )
            continue
        wheel_hashes = {w["sha256"] for w in ws}
        for h in e["hashes"]:
            if h not in wheel_hashes:
                problems.append(
                    "lock {}=={} 의 hash {}... 에 해당하는 wheel 이 없습니다".format(
                        e["name"], e["version"], h[:12]
                    )
                )
        for w in ws:
            if w["sha256"] not in e["hashes"]:
                problems.append("wheel {} 의 sha256 이 lock 과 다릅니다".format(w["filename"]))
    for w in wheels:
        if (w["name"], w["version"]) not in lock_keys:
            problems.append("wheel {} 이 lock 에 없습니다".format(w["filename"]))
    return problems


# ----------------------------------------------------------------------------- checksums.sha256
def parse_checksums(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for i, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([0-9a-f]{64})\s+\*?(.+)$", line)
        if not m:
            raise ScriptError("E_PACKAGE_INVALID", f"{CHECKSUMS_NAME} {i}행 형식 오류")
        path = m.group(2).strip().replace("\\", "/")
        if path in out:
            raise ScriptError("E_PACKAGE_INVALID", f"{CHECKSUMS_NAME} 중복 경로: {path}")
        out[path] = m.group(1)
    return out


def format_checksums(entries: dict[str, str]) -> str:
    return "".join(f"{entries[p]}  {p}\n" for p in sorted(entries))


# ----------------------------------------------------------------------------- 호스트/타깃 프로파일
PROBE_SNIPPET = (
    "import json,platform,struct,sys\n"
    "d={'python_version':platform.python_version(),'python_implementation':platform.python_implementation(),"
    "'bits':struct.calcsize('P')*8,'executable':sys.executable,'prefix':sys.prefix,'base_prefix':getattr(sys,'base_prefix',sys.prefix),"
    "'system':platform.system(),'machine':platform.machine(),'release':platform.release(),'version':platform.version()}\n"
    "try:\n import venv\n d['has_venv']=True\nexcept Exception:\n d['has_venv']=False\n"
    "try:\n import ensurepip\n d['has_ensurepip']=True\n d['ensurepip_version']=ensurepip.version()\nexcept Exception:\n d['has_ensurepip']=False\n"
    "try:\n import pip\n d['pip_version']=pip.__version__\nexcept Exception:\n d['pip_version']=None\n"
    "try:\n d['libc']=platform.libc_ver()\nexcept Exception:\n d['libc']=('','')\n"
    "print(json.dumps(d))\n"
)


def probe_python(python: str, timeout: int = 60) -> Optional[dict[str, Any]]:
    """다른 Python 실행 파일의 버전/비트/venv/ensurepip 유무를 subprocess 로 조사. 실행 불가면 None."""
    try:
        proc = subprocess.run(
            [python, "-I", "-c", PROBE_SNIPPET],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=sanitized_env(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None
    return data if isinstance(data, dict) else None


def _norm_os(value: str) -> str:
    v = (value or "").strip().lower()
    if v in ("windows", "win", "win32", "nt"):
        return "windows"
    if v in ("linux", "linux2"):
        return "linux"
    if v in ("darwin", "macos", "mac", "osx"):
        return "macos"
    return v


def _norm_arch(value: str) -> str:
    v = (value or "").strip().lower()
    if v in ("amd64", "x86_64", "x64", "em64t"):
        return "x64"
    if v in ("arm64", "aarch64"):
        return "arm64"
    return v


def _os_key(system: str) -> str:
    n = _norm_os(system)
    return {"windows": "win", "linux": "linux", "macos": "macos"}.get(n, n or "unknown")


def profile_id_for(system: str, arch: str, python_abi: str, device: str = "cpu") -> str:
    return f"{_os_key(system)}-{_norm_arch(arch)}-{python_abi}-{device}"


def platform_tags_for(
    system: str, arch: str, libc: tuple[str, str] = ("", ""), mac_ver: str = ""
) -> list[str]:
    n = _norm_os(system)
    a = _norm_arch(arch)
    if n == "windows":
        return ["win_amd64" if a == "x64" else "win_arm64" if a == "arm64" else "win32"]
    if n == "linux":
        march = "x86_64" if a == "x64" else "aarch64" if a == "arm64" else a
        tags = []
        if libc and libc[0] == "glibc" and re.match(r"^\d+\.\d+", libc[1] or ""):
            mj, mn = libc[1].split(".")[:2]
            tags.append(f"manylinux_{int(mj)}_{int(mn)}_{march}")
        tags.append(f"linux_{march}")
        return tags
    if n == "macos":
        march = "x86_64" if a == "x64" else a
        mv = re.match(r"^(\d+)\.(\d+)", mac_ver or "")
        major, minor = (int(mv.group(1)), int(mv.group(2))) if mv else (11, 0)
        return [f"macosx_{major}_{minor}_{march}", f"macosx_{major}_{minor}_universal2"]
    return []


def detect_host(probe: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """현 호스트(또는 probe 한 다른 Python)의 타깃 프로파일 dict. TargetProfile 과 같은 필드 + verified_environment."""
    if probe is None:
        probe = {
            "python_version": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "bits": struct.calcsize("P") * 8,
            "executable": sys.executable,
            "prefix": sys.prefix,
            "base_prefix": getattr(sys, "base_prefix", sys.prefix),
            "system": platform.system(),
            "machine": platform.machine(),
            "release": platform.release(),
            "version": platform.version(),
            "has_venv": _importable("venv"),
            "has_ensurepip": _importable("ensurepip"),
            "pip_version": _module_version("pip"),
            "libc": list(platform.libc_ver()),
        }
    system = str(probe.get("system", ""))
    machine = str(probe.get("machine", ""))
    impl = str(probe.get("python_implementation", "CPython"))
    pyver = str(probe.get("python_version", "0.0.0"))
    parts = pyver.split(".")
    major, minor = (
        (int(parts[0]), int(parts[1]))
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit()
        else (0, 0)
    )
    impl_key = {"CPython": "cp", "PyPy": "pp"}.get(impl, impl.lower()[:2])
    abi = f"{impl_key}{major}{minor}"
    libc = probe.get("libc") or ("", "")
    libc_t = (str(libc[0]), str(libc[1])) if isinstance(libc, (list, tuple)) and len(libc) == 2 else ("", "")
    mac_ver = ""
    if _norm_os(system) == "macos":
        mac_ver = str(probe.get("mac_ver") or platform.mac_ver()[0] or "")
    arch = _norm_arch(machine)
    os_version = (
        str(probe.get("release", "")) if _norm_os(system) != "windows" else str(probe.get("version", ""))
    )
    return {
        "profile_id": profile_id_for(system, arch, abi, "cpu"),
        "os": {"windows": "Windows", "linux": "Linux", "macos": "Darwin"}.get(_norm_os(system), system),
        "os_version": os_version,
        "architecture": arch,
        "python_implementation": impl,
        "python_version": pyver,
        "python_abi": abi,
        "platform_tags": platform_tags_for(system, arch, libc_t, mac_ver),
        "features": list(DEFAULT_FEATURES),
        "device": "cpu",
        "gpu": None,
        "target_confirmed": False,
        "verified_environment": {
            "detected": True,
            "detected_at": now_iso(),
            "machine": machine,
            "bits": int(probe.get("bits", 0) or 0),
            "libc": list(libc_t),
            "has_venv": bool(probe.get("has_venv", False)),
            "has_ensurepip": bool(probe.get("has_ensurepip", False)),
            "pip_version": probe.get("pip_version"),
            "in_virtualenv": str(probe.get("prefix", "")) != str(probe.get("base_prefix", "")),
            "python_executable": str(probe.get("executable", "")),
        },
        "notes": ["호스트에서 자동 감지한 값이며 사내 타깃 확정값이 아닙니다 (target_confirmed=false)."],
    }


def _importable(name: str) -> bool:
    try:
        __import__(name)
        return True
    except Exception:  # noqa: BLE001
        return False


def _module_version(name: str) -> Optional[str]:
    try:
        mod = __import__(name)
        return str(getattr(mod, "__version__", None))
    except Exception:  # noqa: BLE001
        return None


def min_profile(
    profile: dict[str, Any],
    *,
    preflight_status: str = "NOT_RUN",
    feature_status: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    """허용 필드만 담은 최소 요약. 이름/호스트/사용자 경로/URL/파일 목록/인증값을 담지 않는다."""
    out: dict[str, Any] = {
        "format": MIN_PROFILE_FORMAT,
        "generated_at": now_iso(),
        "preflight_status": preflight_status,
    }
    for k in MIN_PROFILE_FIELDS:
        if k in ("format", "generated_at", "preflight_status", "feature_status", "bits"):
            continue
        if k in profile:
            out[k] = profile[k]
    ve = profile.get("verified_environment") or {}
    if isinstance(ve, dict) and "bits" in ve:
        out["bits"] = ve["bits"]
    out["feature_status"] = dict(feature_status or {})
    # 경로/호스트명 같은 값이 섞이지 않도록 문자열 필드를 한 번 더 검사한다 (format 은 고정 상수)
    for k, v in list(out.items()):
        if k == "format":
            continue
        if isinstance(v, str) and ("/" in v or "\\" in v or "://" in v):
            out[k] = "REDACTED"
    return out


def target_mismatches(manifest_target: dict[str, Any], target: dict[str, Any]) -> list[str]:
    """manifest 의 target 과 실제(또는 지정) target 의 OS/arch/구현/버전/ABI 비교."""
    problems: list[str] = []
    if _norm_os(str(manifest_target.get("os", ""))) != _norm_os(str(target.get("os", ""))):
        problems.append("OS 불일치: 패키지 {} / 대상 {}".format(manifest_target.get("os"), target.get("os")))
    if _norm_arch(str(manifest_target.get("architecture", ""))) != _norm_arch(
        str(target.get("architecture", ""))
    ):
        problems.append(
            "아키텍처 불일치: 패키지 {} / 대상 {}".format(
                manifest_target.get("architecture"), target.get("architecture")
            )
        )
    if (
        str(manifest_target.get("python_implementation", "")).lower()
        != str(target.get("python_implementation", "")).lower()
    ):
        problems.append(
            "Python 구현 불일치: 패키지 {} / 대상 {}".format(
                manifest_target.get("python_implementation"), target.get("python_implementation")
            )
        )
    mv = ".".join(str(manifest_target.get("python_version", "")).split(".")[:2])
    tv = ".".join(str(target.get("python_version", "")).split(".")[:2])
    if mv != tv:
        problems.append(f"Python 버전 불일치: 패키지 {mv} / 대상 {tv}")
    if str(manifest_target.get("python_abi", "")) != str(target.get("python_abi", "")):
        problems.append(
            "Python ABI 불일치: 패키지 {} / 대상 {}".format(
                manifest_target.get("python_abi"), target.get("python_abi")
            )
        )
    return problems


# ----------------------------------------------------------------------------- archive 안전
def safe_member_path(member_name: str) -> str:
    """archive member 이름의 traversal 검사. 절대경로/../드라이브 문자/빈 구성요소 거부. 정규화된 posix 경로 반환."""
    name = member_name.replace("\\", "/")
    if not name or name.startswith("/"):
        raise ScriptError("E_PACKAGE_INVALID", f"archive 항목이 절대 경로입니다: {member_name}")
    if len(name) > 1 and name[1] == ":":
        raise ScriptError("E_PACKAGE_INVALID", f"archive 항목에 드라이브 문자가 있습니다: {member_name}")
    parts = name.split("/")
    body = parts[:-1] if name.endswith("/") else parts
    if any(p in ("..", ".", "") for p in body):
        raise ScriptError("E_PACKAGE_INVALID", f"archive 항목에 상위/빈 경로 참조가 있습니다: {member_name}")
    return name


class PackageSource:
    """ZIP 또는 추출된 폴더를 같은 방식으로 읽는다. 파일 목록/크기/hash/추출."""

    def __init__(
        self, path: str | os.PathLike[str], *, max_members: int = 20000, max_ratio: int = 200
    ) -> None:
        p = Path(path).expanduser()
        if not p.exists():
            raise ScriptError("E_PACKAGE_INVALID", f"패키지 경로가 없습니다: {p}")
        self.path = p.resolve()
        self.max_members = max_members
        self.max_ratio = max_ratio
        self.is_zip = p.is_file()
        self._zip: Optional[zipfile.ZipFile] = None
        self._names: dict[str, zipfile.ZipInfo] = {}
        self.links: list[str] = []
        if self.is_zip:
            if not zipfile.is_zipfile(str(p)):
                raise ScriptError("E_PACKAGE_INVALID", f"ZIP 파일이 아닙니다: {p.name}")
            self._zip = zipfile.ZipFile(str(p))
            self._index_zip()
            self.root = self.path
        else:
            root = self.path
            if not (root / MANIFEST_NAME).is_file():
                # 압축 풀기 결과가 하위 폴더 하나에 들어간 경우를 허용
                cands = [c for c in root.iterdir() if c.is_dir() and (c / MANIFEST_NAME).is_file()]
                if len(cands) == 1:
                    root = cands[0]
            self.root = root
            self._index_dir()

    # ---- indexing
    def _index_zip(self) -> None:
        assert self._zip is not None
        infos = self._zip.infolist()
        if len(infos) > self.max_members:
            raise ScriptError(
                "E_PACKAGE_INVALID", f"archive 항목 수가 상한을 초과합니다: {len(infos)} > {self.max_members}"
            )
        for info in infos:
            name = safe_member_path(info.filename)
            mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(mode):
                self.links.append(info.filename)
                continue
            if info.is_dir() or name.endswith("/"):
                continue
            if (
                info.compress_size
                and info.file_size > 1_000_000
                and info.file_size / max(info.compress_size, 1) > self.max_ratio
            ):
                raise ScriptError("E_PACKAGE_INVALID", f"압축 확대 비율이 비정상입니다: {info.filename}")
            if name in self._names:
                raise ScriptError("E_PACKAGE_INVALID", f"archive 에 중복 항목이 있습니다: {name}")
            self._names[name] = info

    def _index_dir(self) -> None:
        self._dir_files: dict[str, Path] = {}
        root = self.root
        if root.is_symlink():
            self.links.append(str(root))
        for dirpath, dirnames, filenames in os.walk(str(root), followlinks=False):
            d = Path(dirpath)
            for dn in list(dirnames):
                if (d / dn).is_symlink():
                    self.links.append(str((d / dn).relative_to(root)).replace("\\", "/"))
                    dirnames.remove(dn)
            for fn in filenames:
                fp = d / fn
                rel = str(fp.relative_to(root)).replace("\\", "/")
                if fp.is_symlink():
                    self.links.append(rel)
                    continue
                self._dir_files[rel] = fp
        if len(self._dir_files) > self.max_members:
            raise ScriptError("E_PACKAGE_INVALID", f"파일 수가 상한을 초과합니다: {len(self._dir_files)}")

    # ---- access
    def list_files(self) -> list[str]:
        return sorted(self._names) if self.is_zip else sorted(self._dir_files)

    def exists(self, rel: str) -> bool:
        return rel in self._names if self.is_zip else rel in self._dir_files

    def size(self, rel: str) -> int:
        if self.is_zip:
            return int(self._names[rel].file_size)
        return self._dir_files[rel].stat().st_size

    def open(self, rel: str) -> Any:
        if self.is_zip:
            assert self._zip is not None
            return self._zip.open(self._names[rel])
        return open(self._dir_files[rel], "rb")

    def read_bytes(self, rel: str) -> bytes:
        with self.open(rel) as f:
            return f.read()

    def hash_and_size(self, rel: str) -> tuple[str, int]:
        with self.open(rel) as f:
            return sha256_stream(f)

    def local_path(self, rel: str) -> Optional[Path]:
        return None if self.is_zip else self._dir_files.get(rel)

    def extract_to(self, dest: Path, rels: Optional[Iterable[str]] = None) -> list[Path]:
        """dest 아래로만 추출 (검사된 이름만). 폴더 원본이면 복사한다."""
        dest = dest.resolve()
        dest.mkdir(parents=True, exist_ok=True)
        out: list[Path] = []
        names = list(rels) if rels is not None else self.list_files()
        for rel in names:
            target = (dest / rel).resolve()
            try:
                target.relative_to(dest)
            except ValueError as exc:
                raise ScriptError("E_PACKAGE_INVALID", f"추출 경로가 대상 폴더를 벗어납니다: {rel}") from exc
            target.parent.mkdir(parents=True, exist_ok=True)
            with self.open(rel) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
            out.append(target)
        return out

    def close(self) -> None:
        if self._zip is not None:
            self._zip.close()
            self._zip = None

    def __enter__(self) -> PackageSource:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


# ----------------------------------------------------------------------------- manifest 검사
_REQUIRED_MANIFEST_FIELDS = (
    "manifest_format",
    "release_id",
    "version",
    "profile_id",
    "package_kind",
    "target",
    "features",
    "schema_version",
    "db_schema_version",
    "verification",
    "files",
    "file_count",
    "total_bytes",
    "prerequisites",
    "created_at",
    "app_distribution",
    "app_module",
    "app_wheel",
    "lock_file",
    "wheelhouse_dir",
    "hash_scope",
)


def manifest_problems(m: Any) -> list[str]:
    """manifest 필수 필드/형식 검사. 문제 목록."""
    p: list[str] = []
    if not isinstance(m, dict):
        return ["manifest 최상위가 객체가 아닙니다"]
    for k in _REQUIRED_MANIFEST_FIELDS:
        if k not in m:
            p.append(f"manifest 필수 필드 누락: {k}")
    if p:
        return p
    if m["manifest_format"] != MANIFEST_FORMAT:
        p.append("manifest_format 불일치: {} (기대 {})".format(m["manifest_format"], MANIFEST_FORMAT))
    if not isinstance(m["release_id"], str) or not RELEASE_ID_RE.match(m["release_id"]):
        p.append("release_id 형식 오류: {!r}".format(m["release_id"]))
    if m["version"] != m["release_id"]:
        p.append("version 과 release_id 가 다릅니다")
    if not isinstance(m["profile_id"], str) or not PROFILE_ID_RE.match(m["profile_id"]):
        p.append("profile_id 형식 오류: {!r}".format(m["profile_id"]))
    if m["package_kind"] not in PACKAGE_KINDS:
        p.append("package_kind 값 오류: {!r}".format(m["package_kind"]))
    t = m["target"]
    if not isinstance(t, dict):
        p.append("target 이 객체가 아닙니다")
    else:
        for k in (
            "profile_id",
            "os",
            "architecture",
            "python_implementation",
            "python_version",
            "python_abi",
            "platform_tags",
        ):
            if k not in t:
                p.append(f"target.{k} 누락")
        if t.get("profile_id") != m["profile_id"]:
            p.append("target.profile_id 와 manifest.profile_id 가 다릅니다")
    if not isinstance(m["features"], list):
        p.append("features 는 목록이어야 합니다")
    if not isinstance(m["schema_version"], str) or not m["schema_version"]:
        p.append("schema_version 형식 오류")
    if not isinstance(m["db_schema_version"], int) or isinstance(m["db_schema_version"], bool):
        p.append("db_schema_version 은 정수여야 합니다")
    v = m["verification"]
    if not isinstance(v, dict):
        p.append("verification 이 객체가 아닙니다")
    else:
        for k in VERIFICATION_KEYS:
            if k not in v:
                p.append(f"verification.{k} 누락")
            elif not isinstance(v[k], dict) or v[k].get("status") not in STATUS_VALUES:
                p.append(f"verification.{k} 상태 값 오류")
        for k in CORP_ONLY_KEYS:
            if isinstance(v.get(k), dict) and v[k].get("status") == "PASS" and not v[k].get("evidence"):
                p.append(f"verification.{k} 가 근거 없이 PASS 입니다")
    files = m["files"]
    if not isinstance(files, list):
        p.append("files 는 목록이어야 합니다")
    else:
        seen = set()
        for i, f in enumerate(files):
            if not isinstance(f, dict) or not all(k in f for k in ("path", "size", "sha256")):
                p.append(f"files[{i}] 형식 오류")
                continue
            try:
                safe_member_path(str(f["path"]))
            except ScriptError as exc:
                p.append(f"files[{i}]: {exc.message}")
            if f["path"] in seen:
                p.append("files 중복 경로: {}".format(f["path"]))
            seen.add(f["path"])
            if not isinstance(f["size"], int) or f["size"] < 0:
                p.append(f"files[{i}].size 오류")
            if not isinstance(f["sha256"], str) or not SHA256_RE.match(f["sha256"]):
                p.append(f"files[{i}].sha256 형식 오류")
        if MANIFEST_NAME in seen or CHECKSUMS_NAME in seen:
            p.append(f"files 에 {MANIFEST_NAME}/{CHECKSUMS_NAME} 가 포함되어 있습니다 (순환 hash)")
        if isinstance(m["file_count"], int) and m["file_count"] != len(files):
            p.append("file_count({}) 와 files 길이({}) 불일치".format(m["file_count"], len(files)))
    if not isinstance(m["prerequisites"], list) or not m["prerequisites"]:
        p.append("prerequisites 가 비어 있습니다 (Python 런타임 등 미포함 선행 조건 명시 필요)")
    for k in ("app_distribution", "app_module", "app_wheel", "lock_file", "wheelhouse_dir", "created_at"):
        if not isinstance(m[k], str) or not m[k]:
            p.append(f"{k} 형식 오류")
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", str(m.get("app_module", ""))):
        p.append("app_module 이 올바른 모듈 이름이 아닙니다")
    return p


def _check(name: str, ok: Optional[bool], detail: str = "") -> dict[str, Any]:
    status = "NOT_RUN" if ok is None else ("PASS" if ok else "FAIL")
    return {"name": name, "status": status, "detail": detail}


def verify_package(
    source: PackageSource,
    *,
    target: Optional[dict[str, Any]] = None,
    install_root: Optional[Path] = None,
    required_free_multiplier: float = 2.0,
    extra_free_bytes: int = 300 * 1024 * 1024,
) -> dict[str, Any]:
    """반입 패키지 검증. 결과 dict: ok/checks/manifest/manifest_sha256/hashes/wheels/lock_entries/target."""
    checks: list[dict[str, Any]] = []
    report: dict[str, Any] = {
        "ok": False,
        "package": str(source.path),
        "is_zip": source.is_zip,
        "checks": checks,
    }
    hashes: dict[str, str] = {}
    sizes: dict[str, int] = {}

    # 1. archive 안전 (traversal/symlink/중복은 PackageSource 생성 시 검사됨)
    checks.append(
        _check(
            "archive_safety",
            not source.links,
            f"symlink/junction 항목: {source.links}" if source.links else "traversal/symlink/중복 없음",
        )
    )

    # 2. manifest
    if not source.exists(MANIFEST_NAME):
        checks.append(_check("manifest_present", False, f"{MANIFEST_NAME} 없음"))
        return report
    manifest_bytes = source.read_bytes(MANIFEST_NAME)
    report["manifest_sha256"] = sha256_bytes(manifest_bytes)
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except ValueError as exc:
        checks.append(_check("manifest_parse", False, f"JSON 파싱 실패: {exc}"))
        return report
    problems = manifest_problems(manifest)
    checks.append(
        _check(
            "manifest_schema",
            not problems,
            "; ".join(problems) if problems else f"필수 필드/형식 OK ({MANIFEST_FORMAT})",
        )
    )
    if problems:
        report["manifest"] = manifest
        return report
    report["manifest"] = manifest

    # 3. 파일 목록/개수/크기/hash
    all_files = set(source.list_files())
    listed = {f["path"]: f for f in manifest["files"]}
    expected_payload = all_files - {MANIFEST_NAME, CHECKSUMS_NAME}
    missing = sorted(set(listed) - all_files)
    extra = sorted(expected_payload - set(listed))
    checks.append(
        _check(
            "file_list",
            not missing and not extra,
            f"manifest 목록에 없는 파일: {extra[:20]}; 패키지에 없는 파일: {missing[:20]}"
            if (missing or extra)
            else f"파일 수 {len(listed)} 일치",
        )
    )
    bad: list[str] = []
    total = 0
    for rel, entry in listed.items():
        if rel not in all_files:
            continue
        h, n = source.hash_and_size(rel)
        hashes[rel] = h
        sizes[rel] = n
        total += n
        if n != entry["size"]:
            bad.append("{}: 크기 {} != manifest {}".format(rel, n, entry["size"]))
        if h != entry["sha256"]:
            bad.append(f"{rel}: sha256 불일치")
    if manifest["total_bytes"] != total and not missing:
        bad.append("total_bytes {} != 실제 {}".format(manifest["total_bytes"], total))
    checks.append(
        _check(
            "file_hashes", not bad, "; ".join(bad[:20]) if bad else f"{len(hashes)}개 파일 크기/sha256 일치"
        )
    )
    report["hashes"] = hashes
    report["total_bytes"] = total

    # 4. checksums.sha256 (manifest 포함, 자기 자신 제외)
    if not source.exists(CHECKSUMS_NAME):
        checks.append(_check("checksums", False, f"{CHECKSUMS_NAME} 없음"))
    else:
        try:
            sums = parse_checksums(source.read_bytes(CHECKSUMS_NAME).decode("utf-8"))
            cbad: list[str] = []
            expected_sum_paths = (all_files - {CHECKSUMS_NAME}) | {MANIFEST_NAME}
            if set(sums) != expected_sum_paths:
                cbad.append(
                    f"checksums 경로 집합 불일치 (없음: {sorted(expected_sum_paths - set(sums))[:10]}, 추가: {sorted(set(sums) - expected_sum_paths)[:10]})"
                )
            if sums.get(MANIFEST_NAME) != report["manifest_sha256"]:
                cbad.append(f"{MANIFEST_NAME} 의 hash 가 checksums 와 다릅니다")
            for rel, h in hashes.items():
                if rel in sums and sums[rel] != h:
                    cbad.append(f"{rel}: checksums hash 불일치")
            checks.append(
                _check(
                    "checksums",
                    not cbad,
                    "; ".join(cbad[:20])
                    if cbad
                    else f"{len(sums)} 항목 일치 (manifest 포함, 자기 자신 제외)",
                )
            )
        except ScriptError as exc:
            checks.append(_check("checksums", False, exc.message))

    # 5. lock
    lock_rel = manifest["lock_file"]
    entries: list[dict[str, Any]] = []
    if not source.exists(lock_rel):
        checks.append(_check("lock_text", False, f"lock 파일 없음: {lock_rel}"))
    else:
        try:
            entries = parse_lock_text(source.read_bytes(lock_rel).decode("utf-8"))
            checks.append(_check("lock_text", True, f"{len(entries)} 항목, URL/VCS/editable/index 옵션 없음"))
        except ScriptError as exc:
            checks.append(
                _check(
                    "lock_text",
                    False,
                    exc.message + (" " + exc.details.get("line", "") if exc.details else ""),
                )
            )
    report["lock_entries"] = entries

    # 6. wheelhouse <-> lock, 앱 wheel
    wh_dir = manifest["wheelhouse_dir"].rstrip("/") + "/"
    wheel_rels = [
        f for f in all_files if f.startswith(wh_dir) and f.endswith(".whl") and "/" not in f[len(wh_dir) :]
    ]
    app_rel = manifest["app_wheel"]
    wheels: list[dict[str, Any]] = []
    wproblems: list[str] = []
    for rel in sorted(wheel_rels) + ([app_rel] if app_rel not in wheel_rels else []):
        if rel not in all_files:
            wproblems.append(f"앱 wheel 이 패키지에 없습니다: {rel}")
            continue
        try:
            info = parse_wheel_filename(rel)
        except ScriptError as exc:
            wproblems.append(exc.message)
            continue
        info["path"] = rel
        info["sha256"] = hashes.get(rel) or source.hash_and_size(rel)[0]
        wheels.append(info)
    if manifest["package_kind"] != "SOURCE_ONLY" and not wheel_rels:
        wproblems.append(f"wheelhouse 가 비어 있습니다: {wh_dir}")
    app_infos = [w for w in wheels if w["path"] == app_rel]
    if app_infos and app_infos[0]["name"] != normalize_name(manifest["app_distribution"]):
        wproblems.append(
            "앱 wheel 이름 {} 이 app_distribution {} 과 다릅니다".format(
                app_infos[0]["name"], manifest["app_distribution"]
            )
        )
    if app_infos and app_infos[0]["version"] != manifest["version"]:
        wproblems.append(
            "앱 wheel 버전 {} 이 manifest version {} 과 다릅니다".format(
                app_infos[0]["version"], manifest["version"]
            )
        )
    if entries:
        wproblems.extend(cross_check_lock(entries, wheels))
        if app_infos and not any(e["name"] == app_infos[0]["name"] for e in entries):
            wproblems.append("앱 wheel 이 lock 에 없습니다")
    elif wheels:
        wproblems.append("lock 을 읽지 못해 wheelhouse 대조를 수행하지 못했습니다")
    checks.append(
        _check(
            "wheelhouse_lock",
            not wproblems,
            "; ".join(wproblems[:20])
            if wproblems
            else f"wheel {len(wheels)}개 ↔ lock {len(entries)}항목 양방향 일치 (앱 wheel 포함)",
        )
    )
    report["wheels"] = wheels

    # 7. 대상 OS/Python/ABI/tag 호환
    tgt = target if target is not None else detect_host()
    report["target"] = {
        k: tgt.get(k)
        for k in (
            "profile_id",
            "os",
            "architecture",
            "python_implementation",
            "python_version",
            "python_abi",
            "platform_tags",
        )
    }
    tproblems = target_mismatches(manifest["target"], tgt)
    for w in wheels:
        tproblems.extend(wheel_compatibility_problems(w, tgt))
    checks.append(
        _check(
            "target_compat",
            not tproblems,
            "; ".join(tproblems[:20])
            if tproblems
            else "대상 {} 과 OS/Python {}/ABI {}/platform {} 호환".format(
                tgt.get("profile_id"),
                tgt.get("python_version"),
                tgt.get("python_abi"),
                tgt.get("platform_tags"),
            ),
        )
    )

    # 8. 디스크 여유
    if install_root is not None:
        free, where = disk_free_bytes(install_root)
        need = int(total * required_free_multiplier) + extra_free_bytes
        if free < 0:
            checks.append(_check("disk_space", None, f"여유 공간 조회 실패: {where}"))
        else:
            checks.append(
                _check(
                    "disk_space",
                    free >= need,
                    f"여유 {free / 1e6:.1f} MB / 필요 {need / 1e6:.1f} MB ({where})",
                )
            )
    else:
        checks.append(_check("disk_space", None, "install_root 미지정"))

    report["ok"] = all(c["status"] != "FAIL" for c in checks)
    return report


def format_verify_report_ko(report: dict[str, Any]) -> str:
    lines = ["패키지 검증: {}".format(report.get("package", ""))]
    m = report.get("manifest") or {}
    if m:
        lines.append(
            "  release {} / profile {} / kind {} / schema {} / db {}".format(
                m.get("release_id"),
                m.get("profile_id"),
                m.get("package_kind"),
                m.get("schema_version"),
                m.get("db_schema_version"),
            )
        )
    for c in report.get("checks", []):
        lines.append("  [{}] {}: {}".format(c["status"], c["name"], c["detail"]))
    lines.append("결과: {}".format("PASS" if report.get("ok") else "FAIL (E_PACKAGE_INVALID)"))
    return "\n".join(lines)


# ----------------------------------------------------------------------------- 설치 root / active.json
def default_install_root() -> Path:
    env = os.environ.get("DIA_INSTALL_ROOT", "").strip()
    if env:
        return Path(env).expanduser()
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "DIA"
    xdg = os.environ.get("XDG_DATA_HOME", "").strip()
    return (Path(xdg) if xdg else Path.home() / ".local" / "share") / "DIA"


def default_data_root(install_root: Path) -> Path:
    env = os.environ.get("DIA_DATA_ROOT", "").strip()
    return Path(env).expanduser() if env else install_root / "workspace"


def default_base_python() -> str:
    """venv 를 만들 기본 Python: PYTHON 환경변수 > (현재가 venv 이면) base 인터프리터 > sys.executable."""
    env = os.environ.get("PYTHON", "").strip()
    if env:
        return env
    if sys.prefix != getattr(sys, "base_prefix", sys.prefix):
        base_exe = getattr(sys, "_base_executable", None)
        if base_exe and Path(base_exe).exists():
            return str(base_exe)
        cand = Path(sys.base_prefix) / ("python.exe" if os.name == "nt" else "bin/python3")
        if cand.exists():
            return str(cand)
    return sys.executable


def venv_python_path(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def read_active(install_root: Path) -> Optional[dict[str, Any]]:
    p = Path(install_root) / ACTIVE_NAME
    if not p.is_file():
        return None
    data = read_json(p)
    return data if isinstance(data, dict) else None


def write_active(install_root: Path, data: dict[str, Any]) -> Path:
    p = Path(install_root) / ACTIVE_NAME
    write_json_atomic(p, data)
    return p


def release_dir(install_root: Path, release_id: str, profile_id: str) -> Path:
    if not RELEASE_ID_RE.match(release_id) or not PROFILE_ID_RE.match(profile_id):
        raise ScriptError(
            "E_PACKAGE_INVALID", f"release_id/profile_id 형식 오류: {release_id} / {profile_id}"
        )
    return Path(install_root) / "releases" / release_id / profile_id


def ensure_layout(install_root: Path, data_root: Path) -> dict[str, str]:
    """company/ workspace/ backups/ 폴더를 만든다 (기존 내용 보존, 삭제/덮어쓰기 없음)."""
    made: dict[str, str] = {}
    for sub in COMPANY_SUBDIRS:
        d = install_root / "company" / sub
        d.mkdir(parents=True, exist_ok=True)
        made[f"company/{sub}"] = str(d)
    for sub in WORKSPACE_SUBDIRS:
        d = data_root / sub
        d.mkdir(parents=True, exist_ok=True)
        made[f"workspace/{sub}"] = str(d)
    (install_root / "backups").mkdir(parents=True, exist_ok=True)
    made["backups"] = str(install_root / "backups")
    return made


# ----------------------------------------------------------------------------- sqlite (상태 DB) 도우미
def _ro_connect(path: Path) -> sqlite3.Connection:
    uri = "file:{}?mode=ro".format(Path(path).resolve().as_posix().replace("?", "%3F"))
    return sqlite3.connect(uri, uri=True, timeout=5.0)


def db_schema_version(path: Path) -> Optional[int]:
    """schema_version 테이블의 MAX(version). 파일 없음 -> None, 테이블 없음 -> 0."""
    p = Path(path)
    if not p.is_file():
        return None
    conn = _ro_connect(p)
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
        ).fetchone()
        if row is None:
            return 0
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        return int(row[0]) if row is not None and row[0] is not None else 0
    finally:
        conn.close()


def active_leases(path: Path, now: Optional[float] = None) -> list[dict[str, Any]]:
    """만료되지 않은 lease 목록 (leases 테이블 없으면 빈 목록)."""
    p = Path(path)
    if not p.is_file():
        return []
    now = time.time() if now is None else now
    conn = _ro_connect(p)
    try:
        row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='leases'").fetchone()
        if row is None:
            return []
        rows = conn.execute(
            "SELECT run_id, worker_id, expires_at FROM leases WHERE expires_at > ?", (now,)
        ).fetchall()
        return [
            {"run_id": r[0], "worker_id": r[1], "expires_in_seconds": round(float(r[2]) - now, 1)}
            for r in rows
        ]
    finally:
        conn.close()


def running_runs(path: Path) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.is_file():
        return []
    conn = _ro_connect(p)
    try:
        row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='runs'").fetchone()
        if row is None:
            return []
        rows = conn.execute(
            "SELECT run_id, status FROM runs WHERE status IN ('RUNNING','EVALUATING','VALIDATING')"
        ).fetchall()
        return [{"run_id": r[0], "status": r[1]} for r in rows]
    finally:
        conn.close()


def sqlite_backup(src: Path, dest: Path) -> dict[str, Any]:
    """sqlite3 backup API 로 일관된 사본 (WAL 내용 포함). temp -> integrity_check -> os.replace."""
    src = Path(src)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.{os.getpid()}.backup-tmp")
    if tmp.exists():
        tmp.unlink()
    conn = sqlite3.connect(str(src), timeout=10.0)
    try:
        dst = sqlite3.connect(str(tmp))
        try:
            conn.backup(dst, pages=0)
        finally:
            dst.close()
    finally:
        conn.close()
    check = sqlite3.connect(str(tmp))
    try:
        integrity = str(check.execute("PRAGMA integrity_check").fetchone()[0])
    finally:
        check.close()
    if integrity != "ok":
        tmp.unlink()
        raise ScriptError("E_INSTALL_FAILED", f"DB 백업 사본 integrity_check 실패: {integrity[:200]}")
    os.replace(tmp, dest)
    return {
        "source": str(src),
        "path": str(dest),
        "sha256": sha256_file(dest),
        "size_bytes": dest.stat().st_size,
        "schema_version": db_schema_version(dest),
        "integrity": integrity,
        "method": "sqlite3.Connection.backup",
        "created_at": now_iso(),
    }


# ----------------------------------------------------------------------------- 설치 (install)
class InstallFailure(ScriptError):
    """설치 단계 실패. 기존 active.json 은 변경되지 않는다."""


def emit(sink: Any, msg: str) -> None:
    """진행 메시지 출력 (sink=None 이면 조용히)."""
    if sink is not None:
        sink(msg)


def resolve_data_root(install_root: Path, explicit: Optional[str], active: Optional[dict[str, Any]]) -> Path:
    """data_root 결정: 명시값 > DIA_DATA_ROOT > active.json 의 data_root > <install_root>/workspace."""
    if explicit:
        return Path(explicit).expanduser()
    env = os.environ.get("DIA_DATA_ROOT", "").strip()
    if env:
        return Path(env).expanduser()
    if active and active.get("data_root"):
        return Path(str(active["data_root"]))
    return install_root / "workspace"


def _log_record(log: dict[str, Any], name: str, result: dict[str, Any], dest: Optional[Path]) -> None:
    entry: dict[str, Any] = {"name": name}
    entry.update(result)
    log["steps"].append(entry)
    if dest is not None and dest.is_dir():
        write_json_atomic(dest / INSTALL_LOG_NAME, log)


def _log_fail(
    log: dict[str, Any],
    dest: Optional[Path],
    code: str,
    message: str,
    *,
    hint: str = "",
    details: Optional[dict[str, Any]] = None,
) -> InstallFailure:
    log["status"] = "install_failed"
    log["finished_at"] = now_iso()
    log["error"] = {"code": code, "message": message, "details": details or {}}
    if dest is not None and dest.is_dir():
        write_json_atomic(dest / INSTALL_LOG_NAME, log)
    return InstallFailure(code, message, hint=hint, details=details)


def _step_ok(
    name: str,
    log: dict[str, Any],
    dest: Optional[Path],
    result: dict[str, Any],
    *,
    code: str = "E_INSTALL_FAILED",
    what: str = "",
) -> None:
    _log_record(log, name, result, dest)
    if result.get("exit_code", 1) != 0:
        raise _log_fail(
            log,
            dest,
            code,
            "{} 실패 (exit {})".format(what or name, result.get("exit_code")),
            hint="releases/<id>/<profile>/install_log.json 의 stderr_tail 을 확인하세요. 기존 활성 버전은 유지됩니다.",
            details={
                "step": name,
                "stderr_tail": str(result.get("stderr_tail", ""))[-1500:],
                "stdout_tail": str(result.get("stdout_tail", ""))[-800:],
            },
        )


def check_base_python(python: str) -> dict[str, Any]:
    """venv 를 만들 base Python 의 선행 조건(실행 가능/venv/ensurepip/64bit). 미충족이면 E_BLOCKED_PREREQUISITE (exit 8)."""
    probe = probe_python(python)
    if probe is None:
        raise InstallFailure(
            "E_BLOCKED_PREREQUISITE",
            f"venv 를 만들 Python 을 실행할 수 없습니다: {python}",
            hint="회사 승인 Python 경로를 --python 또는 PYTHON 환경변수로 지정하세요. 자동 설치/다운로드는 하지 않습니다.",
        )
    if not probe.get("has_venv") or not probe.get("has_ensurepip"):
        raise InstallFailure(
            "E_BLOCKED_PREREQUISITE",
            "Python 에 venv/ensurepip 모듈이 없습니다 (venv={}, ensurepip={})".format(
                probe.get("has_venv"), probe.get("has_ensurepip")
            ),
            hint="venv/ensurepip 가 포함된 회사 승인 CPython 배포본이 필요합니다. 자동 설치/다운로드는 하지 않습니다.",
        )
    if int(probe.get("bits", 0)) != 64:
        raise InstallFailure(
            "E_BLOCKED_PREREQUISITE", "64-bit Python 이 필요합니다 ({}-bit)".format(probe.get("bits"))
        )
    return probe


def files_mismatching_manifest(root: Path, manifest: dict[str, Any]) -> list[str]:
    """root 아래 추출/복사된 파일이 manifest.files 와 (존재/크기/sha256) 다르면 그 경로 목록."""
    bad: list[str] = []
    for f in manifest["files"]:
        fp = root / f["path"]
        if not fp.is_file() or fp.stat().st_size != f["size"] or sha256_file(fp) != f["sha256"]:
            bad.append(str(f["path"]))
    return bad


def pip_install_command(venv_py: Path, wheelhouse: Path, app_wheel_dir: Path, lock: Path) -> list[str]:
    """오프라인 hash-lock 설치 명령 (index/URL/source build/cache 없음)."""
    return [
        str(venv_py),
        "-m",
        "pip",
        "install",
        "--isolated",
        "--no-index",
        "--find-links",
        str(wheelhouse),
        "--find-links",
        str(app_wheel_dir),
        "--require-hashes",
        "--only-binary=:all:",
        "--no-cache-dir",
        "--disable-pip-version-check",
        "--no-input",
        "-r",
        str(lock),
    ]


def run_install(
    *,
    package: str,
    install_root: Path,
    data_root: Path,
    python: str,
    skip_selftest: bool = False,
    activate: bool = True,
    target_profile: Optional[dict[str, Any]] = None,
    timeout: int = 3600,
    sink: Any = None,
) -> dict[str, Any]:
    """반입 패키지 오프라인 설치.

    verify → releases/<release_id>/<profile_id>/ 최종 위치에 추출 (같은 release 가 이미 있고 manifest hash 가 같으면
    재사용, 다르면 중단) → 그 위치에 .venv 생성 (기존 .venv 는 --clear 로 덮어쓰지 않고 중단; venv/ensurepip 없으면 exit 8)
    → pip 환경/설정 차단(sanitized_env) → pip install --isolated --no-index --find-links --require-hashes --only-binary=:all:
    --no-cache-dir --disable-pip-version-check → pip check → import → version smoke → self-test → company/workspace/backups
    폴더 준비(기존 보존) → active.json 원자적 교체(activate=True). 실패 시 기존 active.json 유지, 새 폴더에
    install_log.json(status=install_failed) 기록.
    """
    install_root = Path(install_root).expanduser()
    data_root = Path(data_root).expanduser()
    log: dict[str, Any] = {
        "status": "install_started",
        "started_at": now_iso(),
        "package": str(Path(package).expanduser().resolve()),
        "install_root": str(install_root),
        "data_root": str(data_root),
        "base_python": python,
        "skip_selftest": skip_selftest,
        "steps": [],
    }
    dest: Optional[Path] = None

    # 0. base Python 선행 조건
    probe = check_base_python(python)
    host = detect_host(probe)
    log["python_probe"] = {
        k: probe.get(k)
        for k in (
            "python_version",
            "python_implementation",
            "bits",
            "system",
            "machine",
            "pip_version",
            "ensurepip_version",
        )
    }
    target = target_profile if target_profile is not None else host

    # 1. verify
    emit(sink, "[1/8] 패키지 검증")
    with PackageSource(package) as src:
        report = verify_package(src, target=target, install_root=install_root)
        failed = [ch for ch in report["checks"] if ch["status"] == "FAIL"]
        log["verify"] = {"ok": report["ok"], "checks": report["checks"]}
        if failed:
            raise InstallFailure(
                "E_PACKAGE_INVALID",
                "패키지 검증 실패: "
                + "; ".join("{}: {}".format(ch["name"], ch["detail"]) for ch in failed)[:1500],
                hint="verify.py 로 상세를 확인하세요. 설치는 시작하지 않았고 기존 활성 버전은 그대로입니다.",
                details={"failed": ["{}: {}".format(ch["name"], ch["detail"]) for ch in failed]},
            )
        manifest = report["manifest"]
        manifest_sha = report["manifest_sha256"]
        release_id, profile_id = manifest["release_id"], manifest["profile_id"]
        app_module = manifest["app_module"]
        dest = release_dir(install_root, release_id, profile_id)
        log.update(
            {
                "release_id": release_id,
                "profile_id": profile_id,
                "release_dir": str(dest),
                "manifest_sha256": manifest_sha,
            }
        )
        venv_dir = dest / ".venv"
        venv_py = venv_python_path(venv_dir)

        # 2. 기존 release 폴더 처리 (idempotent / 다른 내용 거부)
        reused = False
        if dest.exists():
            existing_manifest = dest / MANIFEST_NAME
            if not existing_manifest.is_file():
                raise InstallFailure(
                    "E_INSTALL_FAILED",
                    f"release 폴더가 이미 있으나 manifest 가 없습니다 (불완전한 폴더): {dest}",
                    hint="해당 폴더를 확인/정리한 뒤 다시 설치하세요. 설치기는 임의로 삭제하지 않습니다.",
                )
            if sha256_file(existing_manifest) != manifest_sha:
                raise InstallFailure(
                    "E_INSTALL_FAILED",
                    f"같은 release_id {release_id} 가 다른 내용으로 이미 설치되어 있습니다. 조용히 덮어쓰지 않습니다.",
                    hint="새 내용이면 버전을 올려 새 release 로 만들거나, 기존 폴더를 관리자가 확인 후 정리하세요.",
                    details={"release_dir": str(dest)},
                )
            prev_log: dict[str, Any] = {}
            if (dest / INSTALL_LOG_NAME).is_file():
                try:
                    prev_log = read_json(dest / INSTALL_LOG_NAME)
                except (OSError, ValueError):
                    prev_log = {}
            if prev_log.get("status") == "installed" and venv_py.is_file():
                reused = True
                emit(sink, f"동일 release (manifest hash 일치) 가 이미 설치되어 있어 재사용합니다: {dest}")
                log["reused"] = True
                log["steps"] = list(prev_log.get("steps", []))
                _log_record(
                    log,
                    "reuse_existing",
                    {"exit_code": 0, "detail": "manifest sha256 일치, 기존 venv 재사용"},
                    None,
                )
            else:
                if venv_dir.exists():
                    raise InstallFailure(
                        "E_INSTALL_FAILED",
                        f"이전 설치 시도 폴더에 .venv 가 남아 있습니다: {venv_dir}",
                        hint="설치기는 기존 .venv 를 --clear 로 덮어쓰지 않습니다. 실패한 폴더를 관리자가 확인 후 삭제한 뒤 다시 설치하세요.",
                    )
                bad = files_mismatching_manifest(dest, manifest)
                if bad:
                    raise InstallFailure(
                        "E_INSTALL_FAILED",
                        "이전 추출 파일이 manifest 와 다릅니다",
                        details={"files": bad[:20]},
                    )
                log["steps"] = []
                _log_record(
                    log,
                    "reuse_extracted",
                    {
                        "exit_code": 0,
                        "detail": "이전 추출 파일 {}개 재검증 통과".format(len(manifest["files"])),
                    },
                    dest,
                )
        else:
            # 3. 추출 (임시 폴더 → 검증 → rename)
            emit(sink, f"[2/8] 추출: {dest}")
            tmp = dest.parent / f".{dest.name}.extracting-{os.getpid()}"
            if tmp.exists():
                shutil.rmtree(tmp)
            tmp.mkdir(parents=True)
            try:
                src.extract_to(tmp)
                bad = files_mismatching_manifest(tmp, manifest)
                if bad:
                    raise InstallFailure(
                        "E_PACKAGE_INVALID", "추출 결과가 manifest 와 다릅니다", details={"files": bad[:20]}
                    )
                os.replace(tmp, dest)
            except BaseException:
                shutil.rmtree(tmp, ignore_errors=True)
                raise
            _log_record(
                log,
                "extract",
                {"exit_code": 0, "files": len(manifest["files"]), "bytes": report.get("total_bytes")},
                dest,
            )

    if not reused:
        env = sanitized_env()
        # 4. venv (최종 위치, 이동 금지, --clear 없음)
        emit(sink, f"[3/8] venv 생성: {venv_dir}")
        if venv_dir.exists():
            raise _log_fail(
                log,
                dest,
                "E_INSTALL_FAILED",
                f".venv 가 이미 존재합니다: {venv_dir}",
                hint="--clear 로 덮어쓰지 않습니다.",
            )
        r = run_logged([python, "-m", "venv", str(venv_dir)], env=env, timeout=timeout)
        _log_record(log, "venv_create", r, dest)
        if r["exit_code"] != 0 or not venv_py.is_file():
            raise _log_fail(
                log,
                dest,
                "E_BLOCKED_PREREQUISITE",
                "venv 생성 실패 (exit {})".format(r["exit_code"]),
                hint="ensurepip/venv 가 있는 회사 승인 Python 인지 확인하세요. 자동 다운로드는 하지 않습니다.",
                details={"stderr_tail": r["stderr_tail"][-1500:]},
            )
        r = run_logged([str(venv_py), "-m", "pip", "--version"], env=env, timeout=timeout)
        _log_record(log, "pip_version", r, dest)
        if r["exit_code"] != 0:
            raise _log_fail(
                log,
                dest,
                "E_BLOCKED_PREREQUISITE",
                "새 venv 에 pip 이 없습니다 (ensurepip 실패)",
                details={"stderr_tail": r["stderr_tail"][-1500:]},
            )

        # 5. pip install (offline, hash lock)
        emit(sink, "[4/8] 오프라인 설치 (--no-index --require-hashes)")
        wheelhouse = dest / manifest["wheelhouse_dir"]
        lock = dest / manifest["lock_file"]
        app_wheel_dir = (dest / manifest["app_wheel"]).parent
        cmd = pip_install_command(venv_py, wheelhouse, app_wheel_dir, lock)
        r = run_logged(cmd, env=env, cwd=str(dest), timeout=timeout, tail_lines=80)
        _step_ok("pip_install", log, dest, r, what="pip 오프라인 설치")
        emit(sink, "[5/8] pip check / import / version")
        _step_ok(
            "pip_check",
            log,
            dest,
            run_logged([str(venv_py), "-m", "pip", "check"], env=env, timeout=timeout),
            what="pip check",
        )
        _step_ok(
            "import_app",
            log,
            dest,
            run_logged(
                [str(venv_py), "-c", f"import {app_module}"], env=env, cwd=str(install_root), timeout=timeout
            ),
            what="앱 import",
        )
        r = run_logged(
            [str(venv_py), "-m", app_module, "version", "--json"],
            env=env,
            cwd=str(install_root),
            timeout=timeout,
        )
        _step_ok("smoke_version", log, dest, r, what="version smoke")
        try:
            vinfo = json.loads(r["stdout_tail"].strip().splitlines()[-1])
            reported = str(vinfo.get("version"))
        except (ValueError, IndexError, AttributeError):
            reported = "?"
        if reported != manifest["version"]:
            _log_record(
                log,
                "smoke_version_match",
                {"exit_code": 1, "reported": reported, "expected": manifest["version"]},
                dest,
            )
            raise _log_fail(
                log,
                dest,
                "E_INSTALL_FAILED",
                "설치된 앱 버전 {} 이 manifest {} 과 다릅니다".format(reported, manifest["version"]),
            )
        _log_record(log, "smoke_version_match", {"exit_code": 0, "reported": reported}, dest)

        # 6. self-test
        if skip_selftest:
            emit(sink, "[6/8] self-test 생략 (--skip-selftest)")
            _log_record(
                log,
                "selftest",
                {
                    "exit_code": 0,
                    "status": "skipped",
                    "detail": "--skip-selftest 지정. 설치 후 04_SelfTest.cmd / selftest.py 로 확인하세요.",
                },
                dest,
            )
        else:
            emit(sink, "[6/8] self-test")
            data_root.mkdir(parents=True, exist_ok=True)
            company = install_root / "company"
            company.mkdir(parents=True, exist_ok=True)
            cmd = [
                str(venv_py),
                "-m",
                app_module,
                "self-test",
                "--json",
                "--output",
                str(dest / "selftest_result.json"),
                "--set",
                f"paths.data_root={data_root}",
                "--set",
                f"paths.company_root={company}",
            ]
            r = run_logged(cmd, env=env, cwd=str(install_root), timeout=timeout, tail_lines=80)
            _step_ok("selftest", log, dest, r, what="self-test")

        log["status"] = "installed"
        log["finished_at"] = now_iso()
        log["venv_python"] = str(venv_py)
        write_json_atomic(dest / INSTALL_LOG_NAME, log)

    # 7. 폴더 구조 (기존 보존)
    emit(sink, "[7/8] company/ workspace/ backups/ 폴더 준비 (기존 보존)")
    layout = ensure_layout(install_root, data_root)
    log["layout"] = layout

    # 8. active.json
    result: dict[str, Any] = {
        "ok": True,
        "release_id": release_id,
        "profile_id": profile_id,
        "release_dir": str(dest),
        "venv_python": str(venv_py),
        "install_root": str(install_root),
        "data_root": str(data_root),
        "app_module": app_module,
        "manifest": manifest,
        "reused": reused,
        "activated": False,
        "log_path": str(dest / INSTALL_LOG_NAME),
    }
    if activate:
        emit(sink, "[8/8] active.json 전환")
        result["active"] = activate_release(install_root, data_root, manifest, dest, venv_py)
        result["activated"] = True
    else:
        emit(sink, "[8/8] active.json 전환 보류 (--no-activate)")
    return result


def activate_release(
    install_root: Path,
    data_root: Path,
    manifest: dict[str, Any],
    dest: Path,
    venv_py: Path,
    *,
    reason: str = "install",
) -> dict[str, Any]:
    """active.json 을 원자적으로 교체한다 (모든 검사 통과 후에만 호출). 이미 같은 release/venv 를 가리키면 그대로 둔다."""
    prev = read_active(install_root)
    if (
        prev
        and str(prev.get("release_dir")) == str(dest)
        and str(prev.get("venv_python")) == str(venv_py)
        and str(prev.get("data_root")) == str(data_root)
    ):
        unchanged = dict(prev)
        unchanged["unchanged"] = True
        return unchanged
    active = {
        "release_id": manifest["release_id"],
        "profile_id": manifest["profile_id"],
        "venv_python": str(venv_py),
        "release_dir": str(dest),
        "install_root": str(install_root),
        "data_root": str(data_root),
        "installed_at": now_iso(),
        "schema_version": manifest["schema_version"],
        "db_schema_version": manifest["db_schema_version"],
        "app_version": manifest["version"],
        "app_module": manifest["app_module"],
        "activated_by": reason,
        "previous": {k: prev.get(k) for k in ("release_id", "profile_id", "installed_at", "release_dir")}
        if prev
        else None,
    }
    write_active(install_root, active)
    return active


# ----------------------------------------------------------------------------- 업데이트 (upgrade: backup + migration plan)
MIGRATE_SNIPPET = (
    "import json,sys\n"
    "from {mod}.state.db import StateDB\n"
    "db=StateDB(sys.argv[1])\n"
    "plan=db.migrate()\n"
    "plan['integrity']=db.integrity_check()\n"
    "db.checkpoint(); db.close()\n"
    "print(json.dumps(plan, default=str))\n"
)


def copy_tree_preserve(src: Path, dst: Path) -> int:
    """src 를 dst 로 복사 (symlink 미추적, 기존 파일은 덮어쓰지 않고 새 폴더에만 기록). 파일 수 반환. src 없으면 0."""
    if not src.is_dir():
        return 0
    n = 0
    for dirpath, dirnames, filenames in os.walk(str(src), followlinks=False):
        d = Path(dirpath)
        dirnames[:] = [dn for dn in dirnames if not (d / dn).is_symlink()]
        for fn in filenames:
            fp = d / fn
            if fp.is_symlink():
                continue
            out = dst / fp.relative_to(src)
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(fp, out)
            n += 1
    return n


def make_backup(install_root: Path, data_root: Path, *, label: str = "") -> dict[str, Any]:
    """backups/<시각>[-<label>]/ 에 상태 DB(sqlite backup API) + company/config·templates·extensions 사본 + backup_manifest.json."""
    ts = timestamp_slug()
    bdir = install_root / "backups" / (f"{ts}-{label}" if label else ts)
    i = 1
    while bdir.exists():
        i += 1
        bdir = install_root / "backups" / (f"{ts}-{label}-{i}" if label else f"{ts}-{i}")
    bdir.mkdir(parents=True)
    info: dict[str, Any] = {"backup_dir": str(bdir), "created_at": now_iso(), "db": None, "company": {}}
    db_path = data_root / STATE_DB_RELATIVE
    if db_path.is_file():
        info["db"] = sqlite_backup(db_path, bdir / "state" / db_path.name)
    else:
        info["db"] = {"path": None, "note": "상태 DB 없음 (아직 run 이력 없음)"}
    for sub in COMPANY_SUBDIRS:
        info["company"][sub] = copy_tree_preserve(install_root / "company" / sub, bdir / "company" / sub)
    files: dict[str, str] = {}
    for fp in sorted(p for p in bdir.rglob("*") if p.is_file()):
        files[fp.relative_to(bdir).as_posix()] = sha256_file(fp)
    info["files"] = files
    write_json_atomic(bdir / "backup_manifest.json", info)
    return info


def migration_plan(db_path: Path, target_version: int) -> dict[str, Any]:
    """현재 DB schema version 과 새 release 의 db_schema_version 비교 → action none|migrate|incompatible_newer."""
    current = db_schema_version(db_path)
    if current is None:
        action = "none"
        note = f"상태 DB 없음: 새 버전이 첫 실행 때 schema {target_version} 로 생성"
    elif current == target_version:
        action = "none"
        note = "schema version 동일 (no-op)"
    elif current < target_version:
        action = "migrate"
        note = "사본에 migration 적용·검증 후 교체 (실패 시 이전 DB/active 유지)"
    else:
        action = "incompatible_newer"
        note = f"현재 DB schema {current} 가 새 release 의 {target_version} 보다 새롭습니다. 자동 downgrade 하지 않습니다."
    return {
        "db_path": str(db_path),
        "current": current,
        "target": target_version,
        "action": action,
        "note": note,
        "created_at": now_iso(),
    }


def _assert_wal_quiet(live_db: Path) -> None:
    side = live_db.with_name(live_db.name + "-wal")
    if side.exists() and side.stat().st_size > 0:
        raise ScriptError(
            "E_UPGRADE_LOCKED",
            "상태 DB 의 WAL 이 비어 있지 않습니다 (다른 프로세스가 열고 있을 수 있음)",
            hint="앱을 모두 종료한 뒤 다시 시도하세요.",
        )


def _remove_sidecars(live_db: Path) -> None:
    for suffix in ("-wal", "-shm"):
        side = live_db.with_name(live_db.name + suffix)
        if side.exists():
            side.unlink()


def apply_migration_on_copy(
    plan: dict[str, Any], backup_db: Path, live_db: Path, venv_py: Path, app_module: str, env: dict[str, str]
) -> dict[str, Any]:
    """backup 사본을 복사해 새 venv 로 migration 을 적용·검증(integrity_check)한 뒤 live DB 를 교체한다."""
    work = backup_db.with_name(backup_db.stem + ".migrated" + backup_db.suffix)
    shutil.copy2(backup_db, work)
    r = run_logged(
        [str(venv_py), "-c", MIGRATE_SNIPPET.format(mod=app_module), str(work)], env=env, timeout=1800
    )
    if r["exit_code"] != 0:
        raise ScriptError(
            "E_INSTALL_FAILED", "DB migration (사본) 실패", details={"stderr_tail": r["stderr_tail"][-1500:]}
        )
    try:
        applied = json.loads(r["stdout_tail"].strip().splitlines()[-1])
    except (ValueError, IndexError):
        applied = {}
    if str(applied.get("integrity", "ok")) != "ok":
        raise ScriptError(
            "E_INSTALL_FAILED", "migration 사본 integrity_check 실패", details={"result": applied}
        )
    _assert_wal_quiet(live_db)
    _remove_sidecars(live_db)
    os.replace(work, live_db)
    return {"applied": applied, "replaced": str(live_db), "plan": plan.get("action")}


def run_upgrade(
    *,
    package: str,
    install_root: Path,
    data_root: Path,
    python: str,
    skip_selftest: bool = False,
    sink: Any = None,
) -> dict[str, Any]:
    """새 release 설치(active 미전환) → DB backup + 회사 설정 snapshot → migration_plan.json → 성공 시에만 active 전환.

    1. 상태 DB(<data_root>/state/agent_state.sqlite) 의 만료되지 않은 lease 가 있으면 E_UPGRADE_LOCKED (exit 6).
    2. run_install(activate=False): releases/<id>/<profile>/ (동일 release 면 재사용).
    3. backups/<시각>-pre-upgrade/ : sqlite backup API 사본 + company/config·templates·extensions + backup_manifest.json.
    4. migration_plan.json: 같으면 no-op, 낮으면 사본에 migration 적용·검증 후 교체, 높으면 E_ROLLBACK_INCOMPATIBLE.
    5. active.json 원자적 전환 + upgrade_log.json. 실패 시 이전 active 유지.
    """
    install_root = Path(install_root).expanduser()
    data_root = Path(data_root).expanduser()
    log: dict[str, Any] = {
        "started_at": now_iso(),
        "package": str(package),
        "install_root": str(install_root),
        "data_root": str(data_root),
        "steps": [],
    }
    previous = read_active(install_root)
    log["previous_active"] = previous
    db_path = data_root / STATE_DB_RELATIVE

    # 1. 실행 중 작업 잠금
    leases = active_leases(db_path)
    running = running_runs(db_path)
    log["steps"].append({"name": "lock_check", "active_leases": leases, "running_runs": running})
    if leases:
        raise ScriptError(
            "E_UPGRADE_LOCKED",
            "실행 중인 작업(lease) 이 있어 업데이트를 시작할 수 없습니다: {}".format(
                ", ".join(str(x["run_id"]) for x in leases)
            ),
            hint="작업을 완료하거나 pause 한 뒤(lease 만료 후) 다시 시도하세요. 실행 중 worker 가 쓰는 파일은 교체하지 않습니다.",
            details={"leases": leases},
        )
    if running:
        log["warnings"] = [f"lease 없이 RUNNING 상태인 run 이 있습니다 (비정상 종료 가능): {running}"]
        emit(sink, f"경고: lease 없이 RUNNING 상태인 run: {running}")

    # 2. 새 release 설치 (active 미전환)
    emit(sink, "새 release 설치 (active 미전환)")
    res = run_install(
        package=package,
        install_root=install_root,
        data_root=data_root,
        python=python,
        skip_selftest=skip_selftest,
        activate=False,
        sink=sink,
    )
    manifest = res["manifest"]
    log["new_release"] = {
        k: res[k] for k in ("release_id", "profile_id", "release_dir", "venv_python", "reused")
    }
    if (
        previous
        and previous.get("release_id") == res["release_id"]
        and previous.get("profile_id") == res["profile_id"]
        and str(previous.get("release_dir")) == str(res["release_dir"])
    ):
        emit(sink, "이미 활성인 release 와 동일합니다 (manifest 일치). 백업/전환은 생략합니다.")
        log["status"] = "already_active"
        log["finished_at"] = now_iso()
        return {
            "ok": True,
            "status": "already_active",
            "release_id": res["release_id"],
            "profile_id": res["profile_id"],
            "previous_release_id": previous.get("release_id"),
            "backup_dir": None,
            "migration_plan": None,
            "log": log,
        }

    # 3. 백업
    emit(sink, "DB backup (sqlite backup API) + company/config·templates·extensions snapshot")
    backup = make_backup(install_root, data_root, label="pre-upgrade")
    log["backup"] = backup
    bdir = Path(backup["backup_dir"])

    # 4. migration plan
    plan = migration_plan(db_path, int(manifest["db_schema_version"]))
    plan["previous_release"] = previous.get("release_id") if previous else None
    plan["new_release"] = res["release_id"]
    write_json_atomic(bdir / "migration_plan.json", plan)
    log["migration_plan"] = plan
    if plan["action"] == "incompatible_newer":
        write_json_atomic(bdir / "upgrade_log.json", dict(log, status="aborted_incompatible"))
        raise ScriptError(
            "E_ROLLBACK_INCOMPATIBLE",
            plan["note"],
            hint="새 release 의 db_schema_version 이 현재 DB 보다 낮습니다. 이전 active 를 유지합니다.",
            details={"migration_plan": str(bdir / "migration_plan.json")},
        )
    if plan["action"] == "migrate":
        if manifest["app_module"] != "corp_dl_agent":
            write_json_atomic(bdir / "upgrade_log.json", dict(log, status="aborted_not_supported"))
            raise ScriptError(
                "E_NOT_SUPPORTED",
                "앱 module {} 의 DB migration 방법을 알지 못합니다".format(manifest["app_module"]),
                hint="이전 active 를 유지합니다.",
            )
        backup_db = bdir / "state" / db_path.name
        emit(sink, "DB migration 을 사본에 적용·검증 후 교체")
        plan["result"] = apply_migration_on_copy(
            plan, backup_db, db_path, Path(res["venv_python"]), manifest["app_module"], sanitized_env()
        )
        write_json_atomic(bdir / "migration_plan.json", plan)

    # 5. active 전환
    emit(sink, "active.json 전환")
    active = activate_release(
        install_root,
        data_root,
        manifest,
        Path(res["release_dir"]),
        Path(res["venv_python"]),
        reason="upgrade",
    )
    log["active"] = active
    log["status"] = "upgraded"
    log["finished_at"] = now_iso()
    write_json_atomic(bdir / "upgrade_log.json", log)
    return {
        "ok": True,
        "status": "upgraded",
        "release_id": res["release_id"],
        "profile_id": res["profile_id"],
        "previous_release_id": previous.get("release_id") if previous else None,
        "backup_dir": str(bdir),
        "migration_plan": plan,
        "log": log,
    }


# ----------------------------------------------------------------------------- 롤백 (rollback: 코드 rollback 과 DB rollback 분리)
def list_db_backups(install_root: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    bdir = install_root / "backups"
    if not bdir.is_dir():
        return out
    for d in sorted(bdir.iterdir()):
        db = d / "state" / "agent_state.sqlite"
        if d.is_dir() and db.is_file():
            out.append(
                {
                    "backup_dir": str(d),
                    "db": str(db),
                    "schema_version": db_schema_version(db),
                    "size_bytes": db.stat().st_size,
                }
            )
    return out


def resolve_restore_db(spec: str) -> Path:
    p = Path(spec).expanduser()
    if p.is_dir():
        cand = p / "state" / "agent_state.sqlite"
        if cand.is_file():
            return cand
        raise ScriptError(
            "E_ROLLBACK_INCOMPATIBLE", f"backup 폴더에 state/agent_state.sqlite 가 없습니다: {p}"
        )
    if p.is_file():
        return p
    raise ScriptError("E_ROLLBACK_INCOMPATIBLE", f"복원할 DB backup 이 없습니다: {p}")


def run_rollback(
    *,
    to: str,
    profile_id: Optional[str],
    install_root: Path,
    data_root: Path,
    restore_db: Optional[str],
    sink: Any = None,
) -> dict[str, Any]:
    """이전 release 로 active.json 을 되돌린다 (재설치 없음).

    - releases/<to>/<profile>/ 의 manifest·venv·설치 상태(installed)를 확인한다.
    - 실행 중 lease 가 있으면 전환하지 않는다 (E_UPGRADE_LOCKED).
    - 현재 DB schema version 이 대상 release 의 db_schema_version 보다 높으면 --restore-db 없이는 E_ROLLBACK_INCOMPATIBLE:
      사용 가능한 backup 목록과 영향(업데이트 이후 이력 손실)을 안내한다.
    - restore_db: 현재 DB 를 backups/<시각>-pre-rollback/ 에 먼저 백업(sqlite backup API)한 뒤 지정 backup 을 복원한다.
      복원 DB 의 schema version 이 대상 release 와 호환되어야 한다. 회사 설정/템플릿은 건드리지 않는다.
    """
    install_root = Path(install_root).expanduser()
    data_root = Path(data_root).expanduser()
    log: dict[str, Any] = {
        "started_at": now_iso(),
        "to": to,
        "install_root": str(install_root),
        "data_root": str(data_root),
    }
    current = read_active(install_root)
    log["previous_active"] = current
    if profile_id is None:
        if current and current.get("profile_id"):
            profile_id = str(current["profile_id"])
        else:
            rel = install_root / "releases" / to
            subs = [d.name for d in rel.iterdir() if d.is_dir()] if rel.is_dir() else []
            if len(subs) != 1:
                raise ScriptError(
                    "E_ROLLBACK_INCOMPATIBLE",
                    f"profile_id 를 결정할 수 없습니다 (--profile 지정 필요): {subs}",
                )
            profile_id = subs[0]
    dest = release_dir(install_root, to, profile_id)
    manifest_path = dest / MANIFEST_NAME
    if not manifest_path.is_file():
        raise ScriptError(
            "E_ROLLBACK_INCOMPATIBLE",
            f"대상 release 가 설치되어 있지 않습니다: {dest}",
            hint="releases/ 아래에 남아 있는 버전만 rollback 할 수 있습니다 (재설치는 install.py).",
        )
    manifest = read_json(manifest_path)
    problems = manifest_problems(manifest)
    if problems:
        raise ScriptError(
            "E_ROLLBACK_INCOMPATIBLE",
            "대상 release 의 manifest 가 올바르지 않습니다",
            details={"problems": problems[:10]},
        )
    venv_py = venv_python_path(dest / ".venv")
    if not venv_py.is_file():
        raise ScriptError("E_ROLLBACK_INCOMPATIBLE", f"대상 release 의 venv python 이 없습니다: {venv_py}")
    inst_log: dict[str, Any] = {}
    if (dest / INSTALL_LOG_NAME).is_file():
        try:
            inst_log = read_json(dest / INSTALL_LOG_NAME)
        except (OSError, ValueError):
            inst_log = {}
    if inst_log.get("status") not in ("installed", None):
        raise ScriptError(
            "E_ROLLBACK_INCOMPATIBLE",
            "대상 release 의 설치 상태가 installed 가 아닙니다: {}".format(inst_log.get("status")),
        )

    db_path = data_root / STATE_DB_RELATIVE
    leases = active_leases(db_path)
    if leases:
        raise ScriptError(
            "E_UPGRADE_LOCKED",
            "실행 중인 작업(lease) 이 있어 전환하지 않습니다: {}".format([x["run_id"] for x in leases]),
            hint="작업 완료/pause 후 다시 시도하세요.",
        )

    target_db_version = int(manifest["db_schema_version"])
    current_db_version = db_schema_version(db_path)
    log["db"] = {
        "path": str(db_path),
        "current_schema_version": current_db_version,
        "target_release_db_schema_version": target_db_version,
    }
    restored: Optional[dict[str, Any]] = None
    if restore_db:
        src_db = resolve_restore_db(restore_db)
        src_version = db_schema_version(src_db)
        if src_version is not None and src_version > target_db_version:
            raise ScriptError(
                "E_ROLLBACK_INCOMPATIBLE",
                f"복원할 backup 의 schema version {src_version} 이 대상 release 의 {target_db_version} 보다 높습니다",
            )
        emit(sink, f"현재 DB 를 pre-rollback 백업 후 {src_db} 을 복원")
        _assert_wal_quiet(db_path)
        pre = make_backup(install_root, data_root, label="pre-rollback")
        log["pre_rollback_backup"] = pre
        db_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = db_path.with_name(f".{db_path.name}.restore-{os.getpid()}")
        shutil.copy2(src_db, tmp)
        if db_schema_version(tmp) != src_version or sha256_file(tmp) != sha256_file(src_db):
            tmp.unlink()
            raise ScriptError("E_ROLLBACK_INCOMPATIBLE", "복원 사본 검증 실패")
        _remove_sidecars(db_path)
        os.replace(tmp, db_path)
        restored = {
            "from": str(src_db),
            "schema_version": src_version,
            "sha256": sha256_file(db_path),
            "pre_rollback_backup": pre["backup_dir"],
        }
        log["restored_db"] = restored
        current_db_version = src_version
    if current_db_version is not None and current_db_version > target_db_version:
        backups = list_db_backups(install_root)
        raise ScriptError(
            "E_ROLLBACK_INCOMPATIBLE",
            f"현재 DB schema {current_db_version} 가 대상 release {to} 의 schema {target_db_version} 보다 새롭습니다. active 포인터만 되돌리면 구버전이 DB 를 읽을 수 없습니다.",
            hint="--restore-db <backups/<시각>> 로 호환되는 DB snapshot 을 복원하면서 rollback 할 수 있습니다. 업데이트 이후 추가된 run/문서 이력은 사라집니다 (현재 DB 는 pre-rollback 백업으로 보존).",
            details={
                "available_backups": [
                    {"backup_dir": b["backup_dir"], "schema_version": b["schema_version"]} for b in backups
                ]
            },
        )
    active = activate_release(install_root, data_root, manifest, dest, venv_py, reason="rollback")
    log["active"] = active
    log["finished_at"] = now_iso()
    rdir = Path(restored["pre_rollback_backup"]) if restored else install_root / "backups"
    rdir.mkdir(parents=True, exist_ok=True)
    log_path = rdir / ("rollback_log.json" if restored else f"rollback-{timestamp_slug()}-{os.getpid()}.json")
    write_json_atomic(log_path, log)
    return {
        "ok": True,
        "release_id": to,
        "profile_id": profile_id,
        "previous_release_id": current.get("release_id") if current else None,
        "restored_db": restored,
        "db_schema_version": current_db_version,
        "log_path": str(log_path),
    }
