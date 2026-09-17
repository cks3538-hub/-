"""반입 ZIP 빌더: dist/DIA_<version>_<profile>.zip + <zip>.sha256.

allowlist 포장:
  source/{corp_dl_agent,tests,pyproject.toml,README.md,CLAUDE.md,docs/CONTRACT.md}
  app/<앱 wheel>  wheelhouse/<profile>/*.whl  locks/<profile>.txt  scripts/  config-examples/  fixtures/  schemas/
  docs/*.md *.txt *.json (+ 프로젝트 루트 BUILD_STATUS.md/BLOCKERS.md)  test-evidence/*.json (raw/ 제외)
  release-manifest.json  checksums.sha256  dependency-inventory.json
금지 패턴 검사(경로: .venv .env .git __pycache__ *.pyc workspace/ dist/ .claude/ ...; 내용: 'sk-' 'BEGIN PRIVATE KEY'
'ghp_' 'xoxb-' 및 '/home/<user>/'·'C:\\Users\\<user>\\' 개인 절대 경로 — 문서 예시 경로 'D:\\...' 은 허용).
acceptance.json 은 ZIP 을 확정한 뒤 scripts/acceptance.py 가 ZIP 밖에 만든다 (ZIP 재포장 금지).
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from corp_dl_agent.common import Status, StatusRecord, atomic_write_text, sha256_file
from corp_dl_agent.errors import AgentError
from corp_dl_agent.packaging import verifier_core as core
from corp_dl_agent.packaging.inventory import build_inventory
from corp_dl_agent.packaging.lockfile import build_lock, scan_wheelhouse, wheel_info
from corp_dl_agent.packaging.manifest import (
    CHECKSUMS_NAME,
    CORP_ONLY_KEYS,
    INVENTORY_NAME,
    MANIFEST_NAME,
    PackageKind,
    ReleaseManifest,
    build_manifest,
    checksums_text,
    compute_file_entries,
    default_verification,
    host_core_status_from_evidence,
    manifest_json,
)
from corp_dl_agent.packaging.target import TargetProfile
from corp_dl_agent.version import CALCULATION_VERSION, DB_SCHEMA_VERSION, SCHEMA_VERSION, __version__

SOURCE_ITEMS: tuple[str, ...] = (
    "corp_dl_agent",
    "tests",
    "pyproject.toml",
    "README.md",
    "CLAUDE.md",
    "docs/CONTRACT.md",
)
TREE_ITEMS: tuple[str, ...] = ("scripts", "config-examples", "fixtures", "schemas")
DOC_GLOBS: tuple[str, ...] = ("*.md", "*.txt", "*.json")
ROOT_DOCS: tuple[str, ...] = ("BUILD_STATUS.md", "BLOCKERS.md")

EXCLUDED_DIR_NAMES: frozenset[str] = frozenset(
    {
        ".venv",
        "venv",
        ".env",
        ".git",
        "__pycache__",
        ".claude",
        "workspace",
        "dist",
        "build",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "node_modules",
        ".idea",
        ".vscode",
        "raw",
    }
)
EXCLUDED_SUFFIXES: frozenset[str] = frozenset(
    {".pyc", ".pyo", ".log", ".pt", ".pth", ".sqlite", ".sqlite-wal", ".sqlite-shm"}
)
EXCLUDED_FILE_NAMES: frozenset[str] = frozenset(
    {".env", ".envrc", ".netrc", "credentials.json", "secrets.yaml", "secrets.json", ".DS_Store", "Thumbs.db"}
)
FORBIDDEN_PATH_COMPONENTS: frozenset[str] = frozenset(
    {
        ".venv",
        "venv",
        ".env",
        ".git",
        "__pycache__",
        ".claude",
        "workspace",
        "dist",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
    }
)

TEXT_SUFFIXES: frozenset[str] = frozenset(
    {
        ".py",
        ".md",
        ".txt",
        ".json",
        ".yaml",
        ".yml",
        ".cmd",
        ".bat",
        ".ps1",
        ".sh",
        ".toml",
        ".cfg",
        ".ini",
        ".csv",
        ".in",
        ".html",
        ".xml",
        ".rst",
    }
)
STORED_SUFFIXES: frozenset[str] = frozenset(
    {".whl", ".zip", ".pptx", ".xlsx", ".docx", ".png", ".jpg", ".gz", ".7z"}
)

# 내용 금지 패턴 (모든 텍스트 파일)
SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "개인키(PEM)"),
    (re.compile(r"ghp_[A-Za-z0-9]{20,}"), "GitHub 토큰"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{20,}"), "GitHub fine-grained 토큰"),
    (re.compile(r"xox[abprs]-[A-Za-z0-9-]{10,}"), "Slack 토큰"),
    (re.compile(r"sk-(?:proj-|ant-)?[A-Za-z0-9_-]{32,}"), "API 키(sk-)"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS access key"),
)
# tests/ 밖에서는 짧은 sk- 문자열도 거부 (tests/ 의 짧은 sk- 값은 마스킹 시험용 합성값)
SHORT_KEY_PATTERN: tuple[re.Pattern[str], str] = (
    re.compile(r"sk-[A-Za-z0-9]{8,}"),
    "API 키 형태 문자열(sk-)",
)
PERSONAL_PATH_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"/home/[A-Za-z0-9._-]+/"), "개인 절대 경로(/home/<user>/)"),
    (re.compile(r"/Users/[A-Za-z0-9._-]+/"), "개인 절대 경로(/Users/<user>/)"),
    (re.compile(r"[A-Za-z]:\\+Users\\+[^\\\s\"']+\\+"), "개인 절대 경로(C:\\Users\\<user>\\)"),
)
MAX_SCAN_BYTES = 8 * 1024 * 1024


@dataclass
class ReleaseBuildResult:
    zip_path: Path
    sha256_path: Path
    zip_sha256: str
    zip_size: int
    manifest: ReleaseManifest
    warnings: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def _copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)


def _copy_tree(src: Path, dst: Path, skipped: list[str]) -> int:
    """allowlist 폴더를 복사하되 제외 폴더/접미사/파일명은 건너뛴다. symlink 는 따라가지 않는다."""
    n = 0
    for dirpath, dirnames, filenames in os.walk(src, followlinks=False):
        d = Path(dirpath)
        keep = []
        for dn in sorted(dirnames):
            if dn in EXCLUDED_DIR_NAMES or dn.endswith(".egg-info") or (d / dn).is_symlink():
                skipped.append(str((d / dn).relative_to(src.parent)))
                continue
            keep.append(dn)
        dirnames[:] = keep
        for fn in sorted(filenames):
            fp = d / fn
            if fp.is_symlink() or fp.suffix in EXCLUDED_SUFFIXES or fn in EXCLUDED_FILE_NAMES:
                skipped.append(str(fp.relative_to(src.parent)))
                continue
            _copy_file(fp, dst / fp.relative_to(src))
            n += 1
    return n


def scan_forbidden(stage: Path) -> list[str]:
    """포장 단계 폴더의 경로/내용 금지 패턴. 문제 목록 (비면 통과)."""
    problems: list[str] = []
    for dirpath, dirnames, filenames in os.walk(stage, followlinks=False):
        d = Path(dirpath)
        for name in [*dirnames, *filenames]:
            rel = (d / name).relative_to(stage).as_posix()
            parts = rel.split("/")
            # test-evidence/raw 는 개인 로컬 보관 (반입 제외)
            if parts[0] == "test-evidence" and len(parts) > 1 and parts[1] == "raw":
                problems.append(f"금지 경로: {rel} (원시 로그는 반입하지 않음)")
            for comp in parts:
                if comp in FORBIDDEN_PATH_COMPONENTS or comp.endswith(".egg-info"):
                    problems.append(f"금지 경로: {rel} ({comp})")
                    break
            if name in EXCLUDED_FILE_NAMES or Path(name).suffix in (".pyc", ".pyo"):
                problems.append(f"금지 파일: {rel}")
        for fn in filenames:
            fp = d / fn
            if fp.suffix.lower() not in TEXT_SUFFIXES:
                continue
            rel = fp.relative_to(stage).as_posix()
            try:
                if fp.stat().st_size > MAX_SCAN_BYTES:
                    continue
                text = fp.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            in_tests = rel.startswith("source/tests/")
            for pat, why in SECRET_PATTERNS:
                if pat.search(text):
                    problems.append(f"금지 내용({why}): {rel}")
            if not in_tests and SHORT_KEY_PATTERN[0].search(text):
                problems.append(f"금지 내용({SHORT_KEY_PATTERN[1]}): {rel}")
            for pat, why in PERSONAL_PATH_PATTERNS:
                m = pat.search(text)
                if m:
                    problems.append(f"금지 내용({why}): {rel} — '{m.group(0)[:40]}'")
    return sorted(set(problems))


def _package_kind(profile: TargetProfile, wheelhouse_dir: Path | None) -> PackageKind:
    if wheelhouse_dir is None:
        return "SOURCE_ONLY"
    return "GPU_OFFLINE" if profile.device == "gpu" else "CPU_OFFLINE"


def build_release_detailed(
    project_root: str | os.PathLike[str],
    *,
    profile: TargetProfile,
    wheelhouse_dir: str | os.PathLike[str] | None,
    app_wheel: str | os.PathLike[str],
    out_dir: str | os.PathLike[str],
    evidence_dir: str | os.PathLike[str] | None = None,
    extra_docs: list[str | os.PathLike[str]] | None = None,
    app_module: str = "corp_dl_agent",
    verification_overrides: dict[str, StatusRecord] | None = None,
    notes: list[str] | None = None,
    db_schema_version: int | None = None,
    schema_version: str | None = None,
) -> ReleaseBuildResult:
    root = Path(project_root).resolve()
    out = Path(out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    app = wheel_info(app_wheel)
    version = app.version
    if app_module == "corp_dl_agent" and version != __version__:
        raise AgentError(
            "E_PACKAGE_INVALID",
            f"앱 wheel 버전 {version} 이 corp_dl_agent.version.__version__ {__version__} 과 다릅니다",
            hint="pyproject.toml/version.py 를 맞춘 뒤 wheel 을 다시 빌드하세요.",
        )
    if not core.RELEASE_ID_RE.match(version):
        raise AgentError("E_PACKAGE_INVALID", f"release_id(버전) 형식 오류: {version}")
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", app_module):
        raise AgentError("E_PACKAGE_INVALID", f"app_module 이름 오류: {app_module}")
    wh = Path(wheelhouse_dir).resolve() if wheelhouse_dir is not None else None
    kind = _package_kind(profile, wh)
    warnings: list[str] = []
    skipped: list[str] = []
    for k, rec in (verification_overrides or {}).items():
        if k in CORP_ONLY_KEYS and rec.status is Status.PASS:
            raise AgentError("E_PACKAGE_INVALID", f"{k} 는 개인 개발 단계에서 PASS 로 표시할 수 없습니다")

    zip_name = f"DIA_{version}_{profile.profile_id}.zip"
    zip_path = out / zip_name
    stage = out / f".stage-{version}-{profile.profile_id}-{os.getpid()}"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    try:
        # 1. source/
        src_dst = stage / "source"
        for item in SOURCE_ITEMS:
            sp = root / item
            if not sp.exists():
                skipped.append(f"source/{item} (없음)")
                continue
            if sp.is_dir():
                _copy_tree(sp, src_dst / item, skipped)
            else:
                _copy_file(sp, src_dst / item)
        if not (src_dst / app_module).exists() and not (root / app_module).exists():
            warnings.append(f"source/ 에 앱 패키지 폴더 {app_module} 가 없습니다 (검토용 소스 미포함)")
        # 2. app/
        app_rel = f"app/{app.filename}"
        _copy_file(Path(app.path), stage / app_rel)
        # 3. wheelhouse/<profile>/
        wh_rel = f"wheelhouse/{profile.profile_id}"
        wheel_count = 0
        if wh is not None:
            wheels = scan_wheelhouse(wh)
            for w in wheels:
                _copy_file(Path(w.path), stage / wh_rel / w.filename)
            wheel_count = len(wheels)
        else:
            (stage / wh_rel).mkdir(parents=True, exist_ok=True)
            warnings.append("wheelhouse 없음: SOURCE_ONLY 패키지 (offline 실행 불가)")
        # 4. locks/<profile>.txt (wheelhouse + 앱 wheel, tag 호환 검사 포함)
        lock_rel = f"locks/{profile.profile_id}.txt"
        if wh is not None:
            build_lock(profile, wh, app.path, out_dir=stage / "locks")
        else:
            app_only = out / f".app-only-wheelhouse-{os.getpid()}"
            try:
                _copy_file(Path(app.path), app_only / app.filename)
                # SOURCE_ONLY: lock 에는 앱 wheel 만 들어간다 (전이 의존성은 미포함 → offline 설치 불가를 manifest 에 표시)
                build_lock(profile, app_only, None, out_dir=stage / "locks")
            finally:
                shutil.rmtree(app_only, ignore_errors=True)
        # 5. scripts/ config-examples/ fixtures/ schemas/
        for item in TREE_ITEMS:
            sp = root / item
            if sp.is_dir():
                _copy_tree(sp, stage / item, skipped)
            else:
                skipped.append(f"{item}/ (없음)")
        # 6. docs/
        docs_src = root / "docs"
        if docs_src.is_dir():
            for pattern in DOC_GLOBS:
                for fp in sorted(docs_src.glob(pattern)):
                    if fp.is_file() and not fp.is_symlink():
                        _copy_file(fp, stage / "docs" / fp.name)
        for name in ROOT_DOCS:
            fp = root / name
            if fp.is_file():
                _copy_file(fp, stage / "docs" / name)
        for extra in extra_docs or []:
            ep = Path(extra)
            if not ep.is_file():
                raise AgentError("E_INPUT_INVALID", f"extra_docs 파일이 없습니다: {ep}")
            _copy_file(ep, stage / "docs" / ep.name)
        # 7. test-evidence/*.json (raw/ 제외)
        host_core = None
        if evidence_dir is not None:
            ev = Path(evidence_dir)
            if ev.is_dir():
                for fp in sorted(ev.glob("*.json")):
                    if fp.is_file():
                        _copy_file(fp, stage / "test-evidence" / fp.name)
                host_core = host_core_status_from_evidence(ev)
            else:
                warnings.append(f"evidence_dir 없음: {ev}")
        if not (stage / "test-evidence").exists():
            (stage / "test-evidence").mkdir()
            atomic_write_text(
                stage / "test-evidence" / "README.txt",
                "포장 전 시험 증거 없음 (NOT_RUN). 최종 ZIP 설치 시험 결과는 외부 acceptance.json 에 기록한다.\n",
            )
        # 8. dependency-inventory.json
        build_inventory(profile, wh, app.path, out_path=stage / INVENTORY_NAME)
        # 9. 금지 패턴 검사 (manifest/checksums 생성 전)
        problems = scan_forbidden(stage)
        if problems:
            raise AgentError(
                "E_PACKAGE_INVALID",
                "포장 대상에 금지 패턴이 있습니다 (개인 secret/절대 경로/개발 산출물)",
                hint="해당 파일을 정리하거나 allowlist 밖으로 옮긴 뒤 다시 빌드하세요.",
                details={"problems": problems[:50]},
            )
        # 10. manifest + checksums
        entries = compute_file_entries(stage)
        verification = default_verification(
            profile=profile,
            bundle_reason=(
                f"wheelhouse {wheel_count}개 wheel + 앱 wheel + lock(hash) 준비, {profile.profile_id} tag 호환 검사 통과. "
                "cross-download 는 파일 확보이며 타깃 실행 시험이 아니다."
                if wh is not None
                else "SOURCE_ONLY: wheelhouse 없음"
            ),
            host_core=host_core,
        )
        if wh is None:
            verification["TARGET_BUNDLE_PREPARED"] = StatusRecord(
                status=Status.SOURCE_ONLY, reason="wheelhouse 없이 소스/앱 wheel 만 포장"
            )
        for k, rec in (verification_overrides or {}).items():
            verification[k] = rec
        manifest = build_manifest(
            profile=profile,
            version=version,
            package_kind=kind,
            app_distribution=app.name,
            app_module=app_module,
            app_wheel=app_rel,
            lock_file=lock_rel,
            wheelhouse_dir=wh_rel,
            wheel_count=wheel_count,
            verification=verification,
            files=entries,
            schema_version=schema_version if schema_version is not None else SCHEMA_VERSION,
            db_schema_version=db_schema_version if db_schema_version is not None else DB_SCHEMA_VERSION,
            calculation_version=CALCULATION_VERSION if app_module == "corp_dl_agent" else None,
            build_host={
                "os": platform.system(),
                "os_release": platform.release(),
                "architecture": platform.machine(),
                "python": platform.python_version(),
                "python_implementation": platform.python_implementation(),
            },
            notes=[
                "release_id == version. 설치 폴더는 releases/<release_id>/<profile_id>/ 로 제한된다.",
                "동일 release_id 재설치는 release-manifest.json 의 sha256 이 같을 때만 재사용한다.",
                *(notes or []),
            ],
        )
        mtext = manifest_json(manifest)
        atomic_write_text(stage / MANIFEST_NAME, mtext)
        atomic_write_text(
            stage / CHECKSUMS_NAME,
            checksums_text(entries, manifest_sha256=core.sha256_bytes(mtext.encode("utf-8"))),
        )
        # 11. 자체 검증 (빌더 결과를 verifier 로 한 번 더 확인)
        with core.PackageSource(stage) as src:
            report = core.verify_package(src, target=profile.to_core_dict(), install_root=None)
        failed = [c for c in report["checks"] if c["status"] == "FAIL"]
        if failed:
            raise AgentError(
                "E_PACKAGE_INVALID",
                "빌드 결과 자체 검증 실패",
                details={"failed": [f"{c['name']}: {c['detail']}" for c in failed]},
            )
        # 12. ZIP
        tmp_zip = out / f".{zip_name}.{os.getpid()}.tmp"
        if tmp_zip.exists():
            tmp_zip.unlink()
        with zipfile.ZipFile(
            tmp_zip, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True, strict_timestamps=False
        ) as zf:
            for fp in sorted(p for p in stage.rglob("*") if p.is_file()):
                rel = fp.relative_to(stage).as_posix()
                ctype = zipfile.ZIP_STORED if fp.suffix.lower() in STORED_SUFFIXES else zipfile.ZIP_DEFLATED
                zf.write(fp, rel, compress_type=ctype)
        os.replace(tmp_zip, zip_path)
        digest = sha256_file(zip_path)
        sha_path = zip_path.with_name(zip_path.name + ".sha256")
        atomic_write_text(sha_path, f"{digest}  {zip_path.name}\n")
        return ReleaseBuildResult(
            zip_path=zip_path,
            sha256_path=sha_path,
            zip_sha256=digest,
            zip_size=zip_path.stat().st_size,
            manifest=manifest,
            warnings=warnings,
            skipped=skipped,
        )
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def build_release(
    project_root: str | os.PathLike[str],
    profile: TargetProfile,
    wheelhouse_dir: str | os.PathLike[str] | None,
    app_wheel: str | os.PathLike[str],
    out_dir: str | os.PathLike[str],
    evidence_dir: str | os.PathLike[str] | None = None,
    extra_docs: list[str | os.PathLike[str]] | None = None,
    **kwargs: Any,
) -> Path:
    """반입 ZIP 을 만들고 경로를 반환한다 (dist/DIA_<version>_<profile>.zip, 옆에 <zip>.sha256)."""
    return build_release_detailed(
        project_root,
        profile=profile,
        wheelhouse_dir=wheelhouse_dir,
        app_wheel=app_wheel,
        out_dir=out_dir,
        evidence_dir=evidence_dir,
        extra_docs=extra_docs,
        **kwargs,
    ).zip_path
