"""doctor: 환경/패키지/경로/프로파일/연결 상태 진단. 어떤 패키지가 없어도 예외 없이 완료한다.

출력 원칙: secret 값은 마스킹, 최소 진단 요약(export_diagnostics_preview) 에는 허용 필드만 포함.
"""

from __future__ import annotations

import importlib
import os
import platform
import sys
import sysconfig
import tempfile
from pathlib import Path
from typing import Any

from corp_dl_agent.common import Status, StatusRecord, now_iso
from corp_dl_agent.config.loader import mask_config
from corp_dl_agent.config.profiles import PROFILE_POLICY
from corp_dl_agent.config.schemas import AppConfig
from corp_dl_agent.version import DB_SCHEMA_VERSION, SCHEMA_VERSION, __version__
from corp_dl_agent.workspace import Workspace

PACKAGES: dict[str, tuple[str, str]] = {
    # import 이름: (표시 이름, 기능 묶음)
    "pydantic": ("pydantic", "core"),
    "yaml": ("PyYAML", "core"),
    "httpx": ("httpx", "core"),
    "pptx": ("python-pptx", "documents"),
    "openpyxl": ("openpyxl", "documents"),
    "numpy": ("numpy", "ml-cpu"),
    "pandas": ("pandas", "ml-cpu"),
    "sklearn": ("scikit-learn", "ml-cpu"),
    "torch": ("torch", "ml-cpu"),
    "win32com": ("pywin32", "cad-windows"),
}


def _try_import(name: str) -> dict[str, Any]:
    try:
        mod = importlib.import_module(name)
    except Exception as exc:  # noqa: BLE001 - 진단은 어떤 import 실패도 보고만 한다
        return {"installed": False, "version": None, "error": f"{type(exc).__name__}: {str(exc)[:120]}"}
    return {"installed": True, "version": getattr(mod, "__version__", None), "error": None}


def python_abi_tag() -> str:
    return f"cp{sys.version_info.major}{sys.version_info.minor}"


def host_info() -> dict[str, Any]:
    return {
        "os": platform.system(),
        "os_version": platform.version(),
        "os_release": platform.release(),
        "architecture": platform.machine(),
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "python_abi": python_abi_tag(),
        "python_executable": sys.executable,
        "in_virtualenv": sys.prefix != getattr(sys, "base_prefix", sys.prefix),
        "platform_tag": sysconfig.get_platform(),
        "cpu_count": os.cpu_count(),
    }


def torch_status() -> dict[str, Any]:
    info = _try_import("torch")
    if not info["installed"]:
        return {"status": "NOT_INSTALLED", **info}
    try:
        import torch

        cuda = bool(torch.cuda.is_available())
        return {
            "status": "OK",
            "version": torch.__version__,
            "cuda_available": cuda,
            "cuda_version": getattr(torch.version, "cuda", None),
            "device_default": "cpu",
            "gpu_names": [torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())]
            if cuda
            else [],
        }
    except Exception as exc:  # noqa: BLE001
        return {"status": "ERROR", "error": f"{type(exc).__name__}: {str(exc)[:200]}"}


def _check_writable(path: Path) -> dict[str, Any]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=str(path), prefix=".doctor-", suffix=".tmp", delete=True) as f:
            f.write(b"ok")
        return {"path": str(path), "writable": True}
    except OSError as exc:
        return {"path": str(path), "writable": False, "error": str(exc)[:200]}


def _korean_path_check(base: Path) -> dict[str, Any]:
    try:
        p = base / "한글 경로 검사"
        p.mkdir(parents=True, exist_ok=True)
        f = p / "파일 이름.txt"
        f.write_text("ok", encoding="utf-8")
        ok = f.read_text(encoding="utf-8") == "ok"
        f.unlink()
        p.rmdir()
        return {"supported": ok}
    except OSError as exc:
        return {"supported": False, "error": str(exc)[:200]}


def adapter_status(cfg: AppConfig) -> dict[str, Any]:
    out: dict[str, Any] = {}
    # CAD live adapters
    try:
        cad = importlib.import_module("corp_dl_agent.adapters.cad")
        for name in ("CatiaV5ComAdapter", "ThreeDExperienceAdapter"):
            cls = getattr(cad, name, None)
            if cls is None:
                out[name] = StatusRecord(status=Status.NOT_RUN, reason="adapter 클래스 없음").model_dump(
                    mode="json"
                )
                continue
            try:
                inst = cls()
                avail = bool(inst.available())
                reason = getattr(inst, "status_reason", None) or (
                    "사용 가능" if avail else "INACTIVE: 설치 제품/COM/라이선스 미확인"
                )
                out[name] = StatusRecord(
                    status=Status.PASS if avail else Status.BLOCKED, reason=str(reason)
                ).model_dump(mode="json")
            except Exception as exc:  # noqa: BLE001
                out[name] = StatusRecord(
                    status=Status.BLOCKED, reason=f"INACTIVE: {type(exc).__name__}"
                ).model_dump(mode="json")
    except ImportError:
        out["cad_live"] = StatusRecord(
            status=Status.NOT_RUN, reason="adapters.cad 모듈 없음 (csv_json 수입만 가능)"
        ).model_dump(mode="json")
    out["csv_json_import"] = StatusRecord(status=Status.PASS, reason="구현됨").model_dump(mode="json")
    # LLM gateway
    if cfg.network_allowed():
        out["llm_gateway"] = StatusRecord(
            status=Status.NOT_RUN, reason="설정됨 (live 검증은 integrations doctor 로 사내에서)"
        ).model_dump(mode="json")
    else:
        out["llm_gateway"] = StatusRecord(
            status=Status.NOT_RUN,
            reason=f"프로파일 {cfg.profile.value}: 네트워크 호출 없음 (offline planner)",
        ).model_dump(mode="json")
    # Atlassian
    for prod in ("bitbucket", "jira", "confluence", "bamboo"):
        pc = getattr(cfg.integrations, prod)
        if not pc.enabled:
            out[f"atlassian_{prod}"] = StatusRecord(
                status=Status.NOT_RUN, reason="비활성 (연결 정보 없음)"
            ).model_dump(mode="json")
        else:
            out[f"atlassian_{prod}"] = StatusRecord(
                status=Status.NOT_RUN, reason="설정됨 (live 검증은 사내에서)"
            ).model_dump(mode="json")
    return out


def run_doctor(cfg: AppConfig, *, check_paths: bool = True) -> dict[str, Any]:
    pkgs = {}
    for imp, (disp, feature) in PACKAGES.items():
        info = _try_import(imp)
        pkgs[disp] = {"feature": feature, **info}
    features = {
        "core": all(pkgs[n]["installed"] for n in ("pydantic", "PyYAML", "httpx")),
        "documents": all(pkgs[n]["installed"] for n in ("python-pptx", "openpyxl")),
        "ml-cpu": all(pkgs[n]["installed"] for n in ("numpy", "pandas", "scikit-learn", "torch")),
        "cad-windows": pkgs["pywin32"]["installed"] and platform.system() == "Windows",
    }
    result: dict[str, Any] = {
        "generated_at": now_iso(),
        "app": {
            "version": __version__,
            "schema_version": SCHEMA_VERSION,
            "db_schema_version": DB_SCHEMA_VERSION,
        },
        "host": host_info(),
        "packages": pkgs,
        "features": features,
        "torch": torch_status(),
        "profile": {
            "name": cfg.profile.value,
            "policy": PROFILE_POLICY.get(cfg.profile, {}),
            "network_allowed": cfg.network_allowed(),
        },
        "gateway": mask_config(cfg.gateway.model_dump(mode="json")),
        "integrations": mask_config(cfg.integrations.model_dump(mode="json")),
        "adapters": adapter_status(cfg),
        "config_effective": mask_config(cfg.model_dump(mode="json")),
    }
    if check_paths:
        ws = Workspace.from_config(cfg)
        result["paths"] = {
            "data_root": _check_writable(ws.data_root),
            "state": _check_writable(ws.state),
            "outputs": _check_writable(ws.outputs),
            "company_root": {"path": str(ws.company_root), "exists": ws.company_root.exists()},
            "korean_path": _korean_path_check(ws.data_root) if ws.data_root.exists() else {"supported": None},
        }
    result["summary"] = summarize(result)
    return result


def summarize(result: dict[str, Any]) -> dict[str, str]:
    feats = result.get("features", {})
    s = {
        "core": "PASS" if feats.get("core") else "FAIL",
        "documents": "PASS" if feats.get("documents") else "NOT_INSTALLED",
        "ml-cpu": "PASS" if feats.get("ml-cpu") else "NOT_INSTALLED",
        "gpu": "AVAILABLE" if result.get("torch", {}).get("cuda_available") else "NOT_AVAILABLE",
        "profile": str(result.get("profile", {}).get("name")),
    }
    paths = result.get("paths") or {}
    if paths:
        s["data_root_writable"] = "PASS" if paths.get("data_root", {}).get("writable") else "FAIL"
    return s


ALLOWED_MIN_FIELDS = (
    "os",
    "os_release",
    "architecture",
    "python_implementation",
    "python_version",
    "python_abi",
    "in_virtualenv",
)


def export_diagnostics_preview(result: dict[str, Any]) -> dict[str, Any]:
    """허용 필드만의 로컬 미리보기. 이름/호스트/사용자 경로/URL/파일 목록/인증값 제외. 자동 반출 없음."""
    host = result.get("host", {})
    return {
        "app_version": result.get("app", {}).get("version"),
        "host": {k: host.get(k) for k in ALLOWED_MIN_FIELDS},
        "features": result.get("features"),
        "packages": {
            k: {"installed": v.get("installed"), "version": v.get("version")}
            for k, v in result.get("packages", {}).items()
        },
        "gpu": {"cuda_available": result.get("torch", {}).get("cuda_available", False)},
        "profile": result.get("profile", {}).get("name"),
        "summary": result.get("summary"),
        "note": "이 요약은 로컬 미리보기입니다. 반출은 회사 절차를 따르며 자동 전송하지 않습니다.",
    }


def format_doctor_ko(result: dict[str, Any]) -> str:
    h = result["host"]
    lines = [
        f"corp-dl-agent {result['app']['version']} doctor ({result['generated_at']})",
        f"호스트: {h['os']} {h['os_release']} {h['architecture']} / {h['python_implementation']} {h['python_version']} ({h['python_abi']}) venv={h['in_virtualenv']}",
        "패키지:",
    ]
    for name, info in result["packages"].items():
        lines.append(
            f"  - {name:14s} [{info['feature']:<11s}] {'설치됨 ' + str(info['version'] or '') if info['installed'] else '없음'}"
        )
    t = result["torch"]
    lines.append(f"torch: {t.get('status')} cuda={t.get('cuda_available', False)} (기본 device=cpu)")
    lines.append(
        f"프로파일: {result['profile']['name']} / 네트워크 허용={result['profile']['network_allowed']}"
    )
    for k, v in result["profile"]["policy"].items():
        lines.append(f"  - {k}: {v}")
    lines.append("adapter 상태:")
    for k, v in result["adapters"].items():
        lines.append(f"  - {k}: {v['status']} — {v['reason']}")
    if "paths" in result:
        lines.append("경로:")
        for k, v in result["paths"].items():
            lines.append(f"  - {k}: {v}")
    lines.append("요약: " + ", ".join(f"{k}={v}" for k, v in result["summary"].items()))
    return "\n".join(lines)
