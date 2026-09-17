"""앱 쪽 패키지 검증 (package verify 명령). 검사 로직은 verifier_core (= scripts/_common.py) 와 동일하다."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from corp_dl_agent.errors import AgentError
from corp_dl_agent.packaging import verifier_core as core
from corp_dl_agent.packaging.target import TargetProfile


def verify_package(
    package: str | os.PathLike[str],
    *,
    target: TargetProfile | None = None,
    install_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """ZIP 또는 추출 폴더 검증 보고서. ok=False 이면 checks 에 FAIL 항목이 있다."""
    try:
        with core.PackageSource(package) as src:
            return core.verify_package(
                src,
                target=target.to_core_dict() if target is not None else None,
                install_root=Path(install_root) if install_root is not None else None,
            )
    except core.ScriptError as exc:
        raise AgentError(
            "E_PACKAGE_INVALID", exc.message, hint=exc.hint or None, details=exc.details
        ) from exc


def verify_or_raise(package: str | os.PathLike[str], **kwargs: Any) -> dict[str, Any]:
    report = verify_package(package, **kwargs)
    if not report["ok"]:
        failed = [f"{c['name']}: {c['detail']}" for c in report["checks"] if c["status"] == "FAIL"]
        raise AgentError("E_PACKAGE_INVALID", "반입 패키지 검증 실패", details={"failed": failed})
    return report


def format_report_ko(report: dict[str, Any]) -> str:
    return core.format_verify_report_ko(report)
