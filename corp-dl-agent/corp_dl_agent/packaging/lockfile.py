"""lock 파일 (pip --require-hashes 형식) 생성·검증.

- 형식: `name==version --hash=sha256:<hex>` (이름 정렬). 앱 wheel 을 포함한다.
- validate_lock_text: URL/VCS/-e/디렉터리/--index-url/--extra-index-url/--find-links/-r/-c/--trusted-host 등 거부.
- wheelhouse ↔ lock 양방향 일치, 대상 tag(py3/cp312/abi3/none, win_amd64/manylinux_*_x86_64/linux_x86_64/any) 호환 검사.
파서는 verifier_core (= scripts/_common.py) 를 사용한다.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import Field

from corp_dl_agent.common import StrictModel, atomic_write_text, now_iso, sha256_file
from corp_dl_agent.errors import AgentError
from corp_dl_agent.packaging import verifier_core as core
from corp_dl_agent.packaging.target import TargetProfile


class WheelInfo(StrictModel):
    filename: str
    path: str
    name: str  # PEP 503 정규화
    raw_name: str
    version: str
    build: str | None = None
    python_tags: list[str]
    abi_tags: list[str]
    platform_tags: list[str]
    sha256: str
    size: int

    def core_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class LockEntry(StrictModel):
    name: str
    version: str
    hashes: list[str]
    line: int = 0


class LockCheck(StrictModel):
    ok: bool
    problems: list[str] = Field(default_factory=list)
    entries: int = 0
    wheels: int = 0


def _wrap(exc: core.ScriptError) -> AgentError:
    return AgentError(
        exc.code if exc.code in ("E_PACKAGE_INVALID", "E_NOT_SUPPORTED") else "E_PACKAGE_INVALID",
        exc.message,
        hint=exc.hint or None,
        details=exc.details,
    )


def parse_wheel_filename(filename: str) -> dict[str, Any]:
    try:
        return core.parse_wheel_filename(filename)
    except core.ScriptError as exc:
        raise _wrap(exc) from exc


def wheel_info(path: str | os.PathLike[str]) -> WheelInfo:
    p = Path(path)
    info = parse_wheel_filename(p.name)
    return WheelInfo(path=str(p), sha256=sha256_file(p), size=p.stat().st_size, **info)


def scan_wheelhouse(wheelhouse_dir: str | os.PathLike[str]) -> list[WheelInfo]:
    """wheelhouse 폴더의 *.whl (하위 폴더 제외) 을 파싱한다. wheel 이 아닌 파일이 섞여 있으면 E_PACKAGE_INVALID."""
    d = Path(wheelhouse_dir)
    if not d.is_dir():
        raise AgentError("E_PACKAGE_INVALID", f"wheelhouse 폴더가 없습니다: {d}")
    out: list[WheelInfo] = []
    others: list[str] = []
    for p in sorted(d.iterdir()):
        if p.is_dir() or p.name.startswith("."):
            continue
        if p.suffix != ".whl":
            others.append(p.name)
            continue
        out.append(wheel_info(p))
    if others:
        raise AgentError(
            "E_PACKAGE_INVALID",
            "wheelhouse 에 wheel 이 아닌 파일이 있습니다 (sdist/source build 는 허용하지 않음)",
            details={"files": others[:20]},
        )
    if not out:
        raise AgentError("E_PACKAGE_INVALID", f"wheelhouse 에 wheel 이 없습니다: {d}")
    return out


def lock_text(wheels: list[WheelInfo], *, profile_id: str, app_name: str | None = None) -> str:
    entries = [{"name": w.name, "version": w.version, "hashes": [w.sha256]} for w in wheels]
    header = [
        f"corp-dl-agent lock / profile {profile_id} / generated {now_iso()}",
        "설치: pip install --isolated --no-index --find-links <wheelhouse> --require-hashes --only-binary=:all: --no-cache-dir --disable-pip-version-check -r <이 파일>",
        "모든 항목은 wheelhouse 의 wheel 과 sha256 이 일치해야 하며 URL/VCS/editable/index 옵션은 허용되지 않는다."
        + (f" 앱: {app_name}" if app_name else ""),
    ]
    return core.format_lock_text(entries, header)


def validate_lock_text(text: str) -> list[LockEntry]:
    """lock 텍스트 검증·파싱. 위반 시 E_PACKAGE_INVALID."""
    try:
        return [LockEntry(**e) for e in core.parse_lock_text(text)]
    except core.ScriptError as exc:
        raise _wrap(exc) from exc


def check_lock_against_wheels(entries: list[LockEntry], wheels: list[WheelInfo]) -> list[str]:
    return core.cross_check_lock([e.model_dump() for e in entries], [w.core_dict() for w in wheels])


def check_wheel_compatibility(wheels: list[WheelInfo], profile: TargetProfile) -> list[str]:
    target = profile.to_core_dict()
    problems: list[str] = []
    for w in wheels:
        problems.extend(core.wheel_compatibility_problems(w.core_dict(), target))
    return problems


def build_lock(
    profile: TargetProfile,
    wheelhouse_dir: str | os.PathLike[str],
    app_wheel: str | os.PathLike[str] | None,
    *,
    out_dir: str | os.PathLike[str] | None = None,
) -> Path:
    """wheelhouse + 앱 wheel 로 locks/<profile_id>.txt 를 만든다. tag 비호환 wheel 이 있으면 E_PACKAGE_INVALID.

    out_dir 기본값: <wheelhouse_dir>/../../locks (프로젝트 루트의 locks/).
    """
    wh = Path(wheelhouse_dir)
    wheels = scan_wheelhouse(wh)
    app_name: str | None = None
    if app_wheel is not None:
        app = wheel_info(app_wheel)
        app_name = app.name
        if any(w.name == app.name for w in wheels):
            raise AgentError(
                "E_PACKAGE_INVALID",
                f"wheelhouse 에 앱 wheel 과 같은 이름의 패키지가 이미 있습니다: {app.name}",
            )
        wheels = [*wheels, app]
    problems = check_wheel_compatibility(wheels, profile)
    if problems:
        raise AgentError(
            "E_PACKAGE_INVALID",
            f"프로파일 {profile.profile_id} 와 호환되지 않는 wheel 이 있습니다",
            details={"problems": problems[:30]},
        )
    dup = core.cross_check_lock(
        [{"name": w.name, "version": w.version, "hashes": [w.sha256]} for w in wheels],
        [w.core_dict() for w in wheels],
    )
    if dup:
        raise AgentError("E_PACKAGE_INVALID", "wheelhouse 구성 오류", details={"problems": dup[:30]})
    text = lock_text(wheels, profile_id=profile.profile_id, app_name=app_name)
    validate_lock_text(text)
    out = Path(out_dir) if out_dir is not None else wh.resolve().parent.parent / "locks"
    path = out / f"{profile.profile_id}.txt"
    atomic_write_text(path, text)
    return path


def check_lock_file(
    lock_path: str | os.PathLike[str],
    wheelhouse_dir: str | os.PathLike[str],
    app_wheel: str | os.PathLike[str] | None,
    profile: TargetProfile | None = None,
) -> LockCheck:
    """기존 lock 파일과 wheelhouse(+앱 wheel) 의 양방향 일치·tag 호환을 검사한다."""
    text = Path(lock_path).read_text(encoding="utf-8")
    entries = validate_lock_text(text)
    wheels = scan_wheelhouse(wheelhouse_dir)
    if app_wheel is not None:
        wheels = [*wheels, wheel_info(app_wheel)]
    problems = check_lock_against_wheels(entries, wheels)
    if profile is not None:
        problems.extend(check_wheel_compatibility(wheels, profile))
    return LockCheck(ok=not problems, problems=problems, entries=len(entries), wheels=len(wheels))
