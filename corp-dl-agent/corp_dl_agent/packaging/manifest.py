"""release-manifest.json / checksums.sha256.

hash 정의 (순환 없음, docs/PERSONAL_BUILD_KO.md §4 와 동일):
- release-manifest.json.files: payload 파일 목록/크기/sha256. `checksums.sha256` 과 manifest 자신은 제외.
- checksums.sha256: payload 파일(release-manifest.json 포함) 의 sha256, 자기 자신 제외. 형식 'sha256  path'.
- ZIP 자체의 hash 는 ZIP 밖 `<zip>.sha256` 과 acceptance.json 에만 기록한다.
- hash 는 무결성 확인이며 배포자 진위 확인이 아니다.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from corp_dl_agent.common import (
    Status,
    StatusRecord,
    StrictModel,
    dump_json,
    now_iso,
    read_json,
    sha256_file,
    validate_strict,
)
from corp_dl_agent.errors import AgentError
from corp_dl_agent.packaging import verifier_core as core
from corp_dl_agent.packaging.target import TargetProfile
from corp_dl_agent.version import DB_SCHEMA_VERSION, SCHEMA_VERSION, __version__

MANIFEST_NAME = core.MANIFEST_NAME
CHECKSUMS_NAME = core.CHECKSUMS_NAME
INVENTORY_NAME = core.INVENTORY_NAME
MANIFEST_FORMAT = core.MANIFEST_FORMAT
VERIFICATION_KEYS: tuple[str, ...] = core.VERIFICATION_KEYS
CORP_ONLY_KEYS: tuple[str, ...] = core.CORP_ONLY_KEYS
PackageKind = Literal["SOURCE_ONLY", "CPU_OFFLINE", "GPU_OFFLINE"]

HASH_SCOPE: dict[str, str] = {
    "release-manifest.json.files": "payload 파일 목록/크기/sha256. checksums.sha256 과 release-manifest.json 자신은 제외 (순환 없음).",
    "checksums.sha256": "payload 파일(release-manifest.json 포함) 의 sha256 목록. 자기 자신은 제외. 형식 'sha256  path'.",
    "zip": "ZIP 자체의 sha256 은 ZIP 밖 <zip>.sha256 과 <zip_basename>.acceptance.json 에만 기록 (ZIP 안에 자기 hash 없음).",
    "meaning": "hash 는 무결성 확인이며 배포자 진위 확인이 아니다. 출처는 승인된 전달 경로로 확인한다.",
}

DEFAULT_PREREQUISITES: list[str] = [
    "Python 런타임(회사 승인 CPython, venv/ensurepip 포함) — ZIP 에 포함되지 않음",
    "CATIA V5 / 3DEXPERIENCE (cad extract 시에만; COM/라이선스 현장 확인) — 미포함",
    "Microsoft Office (Excel 재계산/PowerPoint 렌더링 시에만) — 미포함",
    "GPU 드라이버/CUDA (GPU 프로파일에서만) — 미포함, CPU 프로파일은 불필요",
    "문서 렌더러(LibreOffice 등, 승인된 경우만) — 미포함",
    "한국어 폰트(문서 출력용) — 미포함",
]


class FileEntry(StrictModel):
    path: str
    size: int
    sha256: str

    @field_validator("path")
    @classmethod
    def _safe(cls, v: str) -> str:
        try:
            return core.safe_member_path(v)
        except core.ScriptError as exc:
            raise ValueError(exc.message) from exc


class ReleaseManifest(StrictModel):
    manifest_format: str = MANIFEST_FORMAT
    release_id: str
    version: str
    profile_id: str
    package_kind: PackageKind
    target: TargetProfile
    features: list[str]
    schema_version: str
    db_schema_version: int
    calculation_version: str | None = None
    app_distribution: str
    app_module: str
    app_wheel: str
    lock_file: str
    wheelhouse_dir: str
    wheel_count: int = 0
    verification: dict[str, StatusRecord]
    files: list[FileEntry]
    file_count: int
    total_bytes: int
    prerequisites: list[str]
    hash_scope: dict[str, str] = Field(default_factory=lambda: dict(HASH_SCOPE))
    build_host: dict[str, str] = Field(default_factory=dict)
    created_at: str
    notes: list[str] = Field(default_factory=list)

    @field_validator("release_id")
    @classmethod
    def _release_id(cls, v: str) -> str:
        if not core.RELEASE_ID_RE.match(v):
            raise ValueError(f"release_id 형식 오류 (예: 4.0.0): {v}")
        return v

    @model_validator(mode="after")
    def _consistent(self) -> ReleaseManifest:
        if self.version != self.release_id:
            raise ValueError("release_id 는 version 과 같아야 합니다")
        if self.target.profile_id != self.profile_id:
            raise ValueError("target.profile_id 와 profile_id 가 다릅니다")
        missing = [k for k in VERIFICATION_KEYS if k not in self.verification]
        if missing:
            raise ValueError(f"verification 에 상태가 없습니다: {missing}")
        for k in CORP_ONLY_KEYS:
            rec = self.verification[k]
            if rec.status is Status.PASS and not rec.evidence:
                raise ValueError(f"{k} 는 근거(evidence) 없이 PASS 로 표시할 수 없습니다")
        paths = [f.path for f in self.files]
        if len(set(paths)) != len(paths):
            raise ValueError("files 에 중복 경로가 있습니다")
        if MANIFEST_NAME in paths or CHECKSUMS_NAME in paths:
            raise ValueError("files 에는 manifest/checksums 자신을 넣지 않습니다 (순환 hash)")
        if self.file_count != len(self.files):
            raise ValueError("file_count 가 files 길이와 다릅니다")
        if self.total_bytes != sum(f.size for f in self.files):
            raise ValueError("total_bytes 가 files 크기 합과 다릅니다")
        if not self.prerequisites:
            raise ValueError("prerequisites 는 비어 있을 수 없습니다")
        return self


def compute_file_entries(root: str | os.PathLike[str], *, exclude: set[str] | None = None) -> list[FileEntry]:
    """root 아래 모든 파일(symlink 제외)의 상대 posix 경로/크기/sha256. 정렬."""
    r = Path(root)
    ex = set(exclude or ())
    out: list[FileEntry] = []
    for dirpath, dirnames, filenames in os.walk(r, followlinks=False):
        d = Path(dirpath)
        dirnames[:] = sorted(dn for dn in dirnames if not (d / dn).is_symlink())
        for fn in sorted(filenames):
            fp = d / fn
            if fp.is_symlink():
                raise AgentError("E_PACKAGE_INVALID", f"symlink 는 포장하지 않습니다: {fp.relative_to(r)}")
            rel = fp.relative_to(r).as_posix()
            if rel in ex:
                continue
            out.append(FileEntry(path=rel, size=fp.stat().st_size, sha256=sha256_file(fp)))
    return sorted(out, key=lambda e: e.path)


def checksums_text(entries: list[FileEntry], *, manifest_sha256: str) -> str:
    sums = {e.path: e.sha256 for e in entries}
    sums[MANIFEST_NAME] = manifest_sha256
    return core.format_checksums(sums)


def parse_checksums(text: str) -> dict[str, str]:
    try:
        return core.parse_checksums(text)
    except core.ScriptError as exc:
        raise AgentError("E_PACKAGE_INVALID", exc.message) from exc


def default_verification(
    *,
    profile: TargetProfile,
    bundle_reason: str,
    host_core: StatusRecord | None = None,
    target_offline: StatusRecord | None = None,
) -> dict[str, StatusRecord]:
    """6 상태 + TARGET_CONFIRMED. 사내 3 상태는 항상 NOT_RUN (개인 개발 단계에서 PASS 금지)."""
    ts = now_iso()
    v: dict[str, StatusRecord] = {
        "HOST_CORE_TESTED": host_core
        or StatusRecord(status=Status.NOT_RUN, reason="test-evidence 에 pytest 기록 없음", checked_at=ts),
        "TARGET_BUNDLE_PREPARED": StatusRecord(status=Status.PASS, reason=bundle_reason, checked_at=ts),
        "TARGET_OFFLINE_TESTED": target_offline
        or StatusRecord(
            status=Status.NOT_RUN,
            reason=f"동일 target({profile.profile_id}) 환경에서 최종 ZIP 설치 시험 미실행. 결과는 외부 acceptance.json 에 기록한다.",
            checked_at=ts,
        ),
        "CORP_INSTALLED": StatusRecord(
            status=Status.NOT_RUN, reason="사내 PC 에서만 확인 (개인 개발 단계)", checked_at=ts
        ),
        "CORP_INTEGRATED": StatusRecord(
            status=Status.NOT_RUN, reason="사내 gateway/CATIA/Atlassian 연결은 사내에서만 확인", checked_at=ts
        ),
        "BUSINESS_VALIDATED": StatusRecord(
            status=Status.NOT_RUN, reason="실데이터 업무 검증은 사내에서만 수행", checked_at=ts
        ),
        "TARGET_CONFIRMED": StatusRecord(
            status=Status.PASS if profile.target_confirmed else Status.TARGET_UNCONFIRMED,
            reason="사내 타깃 사양 확정"
            if profile.target_confirmed
            else "사내 타깃 미확정: 임시 참조 프로파일",
            checked_at=ts,
        ),
    }
    return v


def host_core_status_from_evidence(evidence_dir: str | os.PathLike[str] | None) -> StatusRecord | None:
    """test-evidence/*.json (raw/ 제외) 의 pytest/ruff/mypy 기록으로 HOST_CORE_TESTED 를 판정한다."""
    if evidence_dir is None:
        return None
    d = Path(evidence_dir)
    if not d.is_dir():
        return None
    records: list[tuple[str, str, int]] = []
    for p in sorted(d.glob("*.json")):
        try:
            data = read_json(p)
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        name = str(data.get("name", p.stem))
        status = str(data.get("status", "NOT_RUN"))
        code = data.get("exit_code", -1)
        records.append((name, status, int(code) if isinstance(code, int) else -1))
    core_records = [r for r in records if any(k in r[0].lower() for k in ("pytest", "core", "ruff", "mypy"))]
    if not core_records:
        return None
    failed = [r[0] for r in core_records if r[1] != "PASS"]
    return StatusRecord(
        status=Status.FAIL if failed else Status.PASS,
        reason=("실패한 시험 기록: " + ", ".join(failed))
        if failed
        else "test-evidence 의 코어 시험 기록이 모두 PASS",
        evidence=[f"test-evidence/{r[0]}.json" for r in core_records],
        checked_at=now_iso(),
    )


def build_manifest(
    *,
    profile: TargetProfile,
    version: str,
    package_kind: PackageKind,
    app_distribution: str,
    app_module: str,
    app_wheel: str,
    lock_file: str,
    wheelhouse_dir: str,
    wheel_count: int,
    verification: dict[str, StatusRecord],
    files: list[FileEntry],
    prerequisites: list[str] | None = None,
    schema_version: str = SCHEMA_VERSION,
    db_schema_version: int = DB_SCHEMA_VERSION,
    calculation_version: str | None = None,
    build_host: dict[str, str] | None = None,
    notes: list[str] | None = None,
) -> ReleaseManifest:
    return ReleaseManifest(
        release_id=version,
        version=version,
        profile_id=profile.profile_id,
        package_kind=package_kind,
        target=profile,
        features=list(profile.features),
        schema_version=schema_version,
        db_schema_version=db_schema_version,
        calculation_version=calculation_version,
        app_distribution=app_distribution,
        app_module=app_module,
        app_wheel=app_wheel,
        lock_file=lock_file,
        wheelhouse_dir=wheelhouse_dir,
        wheel_count=wheel_count,
        verification=verification,
        files=files,
        file_count=len(files),
        total_bytes=sum(f.size for f in files),
        prerequisites=list(prerequisites or DEFAULT_PREREQUISITES),
        build_host=dict(build_host or {}),
        created_at=now_iso(),
        notes=list(notes or []),
    )


def manifest_json(manifest: ReleaseManifest) -> str:
    return dump_json(manifest.model_dump(mode="json")) + "\n"


def load_manifest(source: str | os.PathLike[str] | bytes | dict[str, Any]) -> ReleaseManifest:
    if isinstance(source, dict):
        data: Any = source
    elif isinstance(source, bytes):
        data = core.json.loads(source.decode("utf-8"))
    else:
        data = read_json(source)
    problems = core.manifest_problems(data)
    if problems:
        raise AgentError(
            "E_PACKAGE_INVALID", "release-manifest.json 형식 오류", details={"problems": problems[:30]}
        )
    try:
        return validate_strict(ReleaseManifest, data)
    except ValueError as exc:
        raise AgentError(
            "E_PACKAGE_INVALID", "release-manifest.json 검증 실패", details={"error": str(exc)[:800]}
        ) from exc


APP_VERSION = __version__
