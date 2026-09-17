"""경로 격리: 승인 root 이탈, symlink/junction, archive traversal 검사."""

from __future__ import annotations

import os
import stat
import zipfile
from collections.abc import Iterable
from pathlib import Path

from corp_dl_agent.errors import AgentError


def _is_reparse_point(p: Path) -> bool:
    """Windows junction/reparse point 감지 (POSIX 에서는 항상 False)."""
    try:
        st = os.lstat(p)
    except OSError:
        return False
    attrs = getattr(st, "st_file_attributes", 0)
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attrs & reparse)


def has_link_component(path: Path, stop_at: Path | None = None) -> bool:
    """path 자체 또는 상위 구성요소 중 symlink/junction 이 있으면 True. stop_at 이하만 검사."""
    p = Path(path)
    parts = [p, *p.parents]
    for comp in parts:
        if stop_at is not None:
            try:
                comp.relative_to(stop_at)
            except ValueError:
                break
            if comp == stop_at:
                break
        if comp.is_symlink() or _is_reparse_point(comp):
            return True
    return False


def resolve_within(path: str | os.PathLike[str], roots: Iterable[str | os.PathLike[str]], *, forbid_links: bool = True) -> Path:
    """path 를 resolve 하여 roots 중 하나의 하위인지 확인한다. 아니면 E_PATH_OUTSIDE_ROOT."""
    p = Path(path).expanduser()
    resolved = p.resolve()
    root_list = [Path(r).expanduser().resolve() for r in roots]
    matched = None
    for r in root_list:
        try:
            resolved.relative_to(r)
            matched = r
            break
        except ValueError:
            continue
    if matched is None:
        raise AgentError(
            "E_PATH_OUTSIDE_ROOT",
            f"경로가 승인된 root 밖에 있습니다: {resolved.name}",
            details={"roots": [str(r) for r in root_list], "path": str(resolved)},
        )
    if forbid_links and has_link_component(p if p.is_absolute() else Path.cwd() / p, stop_at=matched):
        raise AgentError("E_PATH_LINK", f"symlink/junction 이 포함된 경로입니다: {resolved.name}")
    return resolved


def safe_member_path(member_name: str) -> str:
    """archive member 이름의 traversal 검사. 절대경로/..//드라이브 문자 거부."""
    name = member_name.replace("\\", "/")
    if not name or name.startswith("/") or name.startswith("//"):
        raise AgentError("E_PACKAGE_INVALID", f"archive 항목이 절대 경로입니다: {member_name}")
    if len(name) > 1 and name[1] == ":":
        raise AgentError("E_PACKAGE_INVALID", f"archive 항목에 드라이브 문자가 있습니다: {member_name}")
    parts = name.split("/")
    if any(part in ("..", "") for part in parts[:-1]) or parts[-1] == "..":
        raise AgentError("E_PACKAGE_INVALID", f"archive 항목에 상위 경로 참조가 있습니다: {member_name}")
    return name


def check_zip_safety(zf: zipfile.ZipFile, *, max_members: int = 20000, max_ratio: int = 200, max_total_bytes: int | None = None) -> list[str]:
    """ZIP 의 traversal/symlink/zip-bomb 검사. 통과한 member 이름 목록을 반환."""
    infos = zf.infolist()
    if len(infos) > max_members:
        raise AgentError("E_PACKAGE_INVALID", f"archive 항목 수가 상한을 초과합니다: {len(infos)} > {max_members}")
    names: list[str] = []
    total = 0
    for info in infos:
        name = safe_member_path(info.filename)
        mode = (info.external_attr >> 16) & 0xFFFF
        if stat.S_ISLNK(mode):
            raise AgentError("E_PACKAGE_INVALID", f"archive 에 symlink 항목이 있습니다: {info.filename}")
        if info.compress_size and info.file_size / max(info.compress_size, 1) > max_ratio and info.file_size > 1_000_000:
            raise AgentError("E_PACKAGE_INVALID", f"압축 확대 비율이 비정상입니다: {info.filename}")
        total += info.file_size
        if max_total_bytes is not None and total > max_total_bytes:
            raise AgentError("E_PACKAGE_INVALID", "archive 전체 크기가 상한을 초과합니다")
        names.append(name)
    return names


def safe_extract(zf: zipfile.ZipFile, dest: Path, **limits: int) -> list[Path]:
    """검사 후 dest 아래로만 추출. 반환: 추출된 파일 경로."""
    names = check_zip_safety(zf, **limits)
    dest = dest.resolve()
    out: list[Path] = []
    for info, name in zip(zf.infolist(), names, strict=True):
        target = (dest / name).resolve()
        try:
            target.relative_to(dest)
        except ValueError as exc:
            raise AgentError("E_PACKAGE_INVALID", f"추출 경로가 대상 폴더를 벗어납니다: {name}") from exc
        if info.is_dir() or name.endswith("/"):
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as src, open(target, "wb") as dst:
            while True:
                chunk = src.read(1 << 20)
                if not chunk:
                    break
                dst.write(chunk)
        out.append(target)
    return out


def assert_managed_delete(path: Path, managed_roots: Iterable[Path]) -> Path:
    """관리 폴더 안의 경로만 삭제를 허용한다."""
    resolved = Path(path).resolve()
    for r in managed_roots:
        try:
            resolved.relative_to(Path(r).resolve())
            return resolved
        except ValueError:
            continue
    raise AgentError("E_PATH_OUTSIDE_ROOT", "관리 폴더 밖의 파일은 삭제하지 않습니다", details={"path": str(resolved)})
