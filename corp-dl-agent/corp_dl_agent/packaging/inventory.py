"""dependency-inventory.json — 재배포하는 wheel 의 이름/버전/라이선스/홈페이지/sha256/파일명/크기/출처.

- wheel 의 *.dist-info/METADATA 를 zipfile 로 읽어 파싱한다 (email header 형식).
- license: License-Expression > License > Classifier(License :: ...) 순. 없으면 "UNKNOWN".
- source: wheelhouse 의 wheel 은 "pypi" (requirements/download_provenance.json 기준), 앱 wheel 은 "local-build".
- approval_state 는 항상 "UNREVIEWED": 회사 승인 여부는 별도 절차이며 공개 코드라고 반입이 승인된 것이 아니다.
"""

from __future__ import annotations

import os
import zipfile
from email.parser import HeaderParser
from pathlib import Path
from typing import Any

from pydantic import Field

from corp_dl_agent.common import StrictModel, atomic_write_json, now_iso
from corp_dl_agent.errors import AgentError
from corp_dl_agent.packaging.lockfile import WheelInfo, scan_wheelhouse, wheel_info
from corp_dl_agent.packaging.target import TargetProfile

INVENTORY_FORMAT = "dia-dependency-inventory/1"
UNKNOWN = "UNKNOWN"


class InventoryItem(StrictModel):
    name: str
    version: str
    filename: str
    sha256: str
    size: int
    license: str = UNKNOWN
    license_source: str = "none"  # License-Expression | License | Classifier | none
    home_page: str = UNKNOWN
    summary: str = ""
    requires_python: str | None = None
    python_tags: list[str] = Field(default_factory=list)
    platform_tags: list[str] = Field(default_factory=list)
    source: str = "pypi"
    approval_state: str = "UNREVIEWED"
    notes: list[str] = Field(default_factory=list)


def read_wheel_metadata(path: str | os.PathLike[str]) -> dict[str, Any]:
    """wheel 의 METADATA 를 파싱하여 {field: [values]} 로 반환. METADATA 가 없으면 빈 dict."""
    p = Path(path)
    try:
        with zipfile.ZipFile(p) as zf:
            names = [n for n in zf.namelist() if n.endswith(".dist-info/METADATA") and n.count("/") == 1]
            if not names:
                return {}
            raw = zf.read(names[0]).decode("utf-8", errors="replace")
    except (OSError, zipfile.BadZipFile) as exc:
        raise AgentError(
            "E_PACKAGE_INVALID", f"wheel 을 읽을 수 없습니다: {p.name}", details={"error": str(exc)[:200]}
        ) from exc
    header_part = raw.split("\n\n", 1)[0]
    msg = HeaderParser().parsestr(header_part)
    out: dict[str, Any] = {}
    for key in msg.keys():
        vals = msg.get_all(key) or []
        out[key] = [str(v).strip() for v in vals]
    return out


def _first(meta: dict[str, Any], key: str) -> str | None:
    vals = meta.get(key) or []
    for v in vals:
        if v and v.upper() != UNKNOWN:
            return str(v)
    return None


def extract_license(meta: dict[str, Any]) -> tuple[str, str]:
    expr = _first(meta, "License-Expression")
    if expr:
        return expr, "License-Expression"
    lic = _first(meta, "License")
    if lic and len(lic) <= 200 and "\n" not in lic:
        return lic, "License"
    classifiers = [c for c in (meta.get("Classifier") or []) if c.startswith("License ::")]
    if classifiers:
        parts = [c.split("::")[-1].strip() for c in classifiers]
        return "; ".join(parts), "Classifier"
    if lic:
        # 긴 라이선스 본문이 License 필드에 들어있는 경우: 첫 줄만 기록
        return lic.splitlines()[0][:120], "License"
    return UNKNOWN, "none"


def extract_home_page(meta: dict[str, Any]) -> str:
    hp = _first(meta, "Home-page")
    if hp:
        return hp
    urls = meta.get("Project-URL") or []
    preferred = ("homepage", "home", "source", "repository", "documentation")
    parsed: list[tuple[str, str]] = []
    for u in urls:
        if "," in u:
            label, url = u.split(",", 1)
            parsed.append((label.strip().lower(), url.strip()))
    for pref in preferred:
        for label, url in parsed:
            if label == pref:
                return url
    if parsed:
        return parsed[0][1]
    return UNKNOWN


def inventory_item(w: WheelInfo, *, source: str = "pypi") -> InventoryItem:
    meta = read_wheel_metadata(w.path)
    notes: list[str] = []
    if not meta:
        notes.append("METADATA 없음")
    lic, lic_src = extract_license(meta)
    return InventoryItem(
        name=_first(meta, "Name") or w.name,
        version=_first(meta, "Version") or w.version,
        filename=w.filename,
        sha256=w.sha256,
        size=w.size,
        license=lic,
        license_source=lic_src,
        home_page=extract_home_page(meta),
        summary=(_first(meta, "Summary") or "")[:200],
        requires_python=_first(meta, "Requires-Python"),
        python_tags=list(w.python_tags),
        platform_tags=list(w.platform_tags),
        source=source,
        approval_state="UNREVIEWED",
        notes=notes,
    )


def build_inventory(
    profile: TargetProfile,
    wheelhouse_dir: str | os.PathLike[str] | None,
    app_wheel: str | os.PathLike[str] | None,
    *,
    out_path: str | os.PathLike[str] | None = None,
    dependency_source: str = "pypi",
) -> dict[str, Any]:
    """dependency-inventory.json 내용을 만들고 (out_path 가 있으면) 기록한다."""
    items: list[InventoryItem] = []
    if wheelhouse_dir is not None:
        for w in scan_wheelhouse(wheelhouse_dir):
            items.append(inventory_item(w, source=dependency_source))
    if app_wheel is not None:
        items.append(inventory_item(wheel_info(app_wheel), source="local-build"))
    licenses: dict[str, int] = {}
    for it in items:
        licenses[it.license] = licenses.get(it.license, 0) + 1
    doc = {
        "format": INVENTORY_FORMAT,
        "profile_id": profile.profile_id,
        "created_at": now_iso(),
        "count": len(items),
        "unknown_license_count": sum(1 for it in items if it.license == UNKNOWN),
        "license_summary": dict(sorted(licenses.items())),
        "approval_notice": "approval_state=UNREVIEWED: 라이브러리 반입/사용 승인은 회사 절차이며 공개 코드라고 자동 승인된 것이 아니다.",
        "source_notice": "source=pypi 는 개인 개발 환경에서 PyPI(https://pypi.org) 로부터 받은 wheel 을 뜻한다 (requirements/download_provenance.json 에 파일별 hash 대조 기록). local-build 는 개인 빌드 앱 wheel.",
        "packages": [it.model_dump(mode="json") for it in items],
    }
    if out_path is not None:
        atomic_write_json(out_path, doc)
    return doc
