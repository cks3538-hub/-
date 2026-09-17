"""설정 로더: package defaults < company config(YAML) < 명시적 CLI override.

- YAML 은 safe_load 만 사용한다.
- secret 은 참조만 저장하며 resolve 는 사용 시점에만, 출력 시에는 마스킹한다.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from corp_dl_agent.common import StrictModel, validate_strict
from corp_dl_agent.config.schemas import AppConfig, Profile
from corp_dl_agent.errors import AgentError

DEFAULT_CONFIG: dict[str, Any] = {
    "schema_version": "4.0",
    "profile": "corp-offline",
}

MASK = "***"
SECRET_KEYS = ("secret_ref", "token", "password", "api_key", "authorization")


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _set_dotted(d: dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    cur = d
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
        if not isinstance(cur, dict):
            raise AgentError(
                "E_CONFIG_INVALID", f"override 경로 '{dotted}' 가 dict 가 아닌 값을 가로지릅니다"
            )
    cur[parts[-1]] = value


def _coerce_cli_value(raw: str) -> Any:
    low = raw.strip().lower()
    if low in ("true", "false"):
        return low == "true"
    if low in ("null", "none", "~"):
        return None
    try:
        if "." in raw:
            return float(raw)
        return int(raw)
    except ValueError:
        pass
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        return [s.strip() for s in inner.split(",") if s.strip()] if inner else []
    return raw


def _format_validation_error(err: ValidationError) -> tuple[str, dict[str, Any]]:
    items = []
    unknown = False
    for e in err.errors():
        loc = ".".join(str(x) for x in e.get("loc", ()))
        msg = e.get("msg", "")
        if e.get("type") == "extra_forbidden":
            unknown = True
        items.append(f"{loc}: {msg}")
    return ("E_CONFIG_UNKNOWN_KEY" if unknown else "E_CONFIG_INVALID"), {"errors": items}


class ConfigLoader(StrictModel):
    config_path: str | None = None
    overrides: dict[str, Any] = {}
    raw_merged: dict[str, Any] = {}

    def build(self) -> AppConfig:
        merged = copy.deepcopy(DEFAULT_CONFIG)
        merged = _deep_merge(merged, installation_defaults())
        if self.config_path:
            p = Path(self.config_path)
            if not p.is_file():
                raise AgentError("E_CONFIG_INVALID", f"설정 파일을 찾을 수 없습니다: {p}")
            try:
                with open(p, encoding="utf-8") as f:
                    loaded = yaml.safe_load(f) or {}
            except yaml.YAMLError as exc:
                raise AgentError(
                    "E_CONFIG_INVALID", f"YAML 파싱 실패: {p}", details={"error": str(exc)[:500]}
                ) from exc
            if not isinstance(loaded, dict):
                raise AgentError("E_CONFIG_INVALID", "설정 파일 최상위는 매핑(dict) 이어야 합니다")
            merged = _deep_merge(merged, loaded)
            merged = _resolve_relative_paths(merged, p.parent)
        for k, v in self.overrides.items():
            _set_dotted(merged, k, v)
        self.raw_merged = merged
        try:
            return validate_strict(AppConfig, merged)
        except ValidationError as exc:
            code, details = _format_validation_error(exc)
            raise AgentError(code, "설정 검증 실패", details=details) from exc


def installation_defaults(environ: dict[str, str] | None = None) -> dict[str, Any]:
    """설치기/실행기(scripts/launch.py)가 넘기는 설치 위치를 '설치 기본값' 으로 반영한다.

    우선순위: package defaults < 설치 기본값(DIA_INSTALL_ROOT/DIA_DATA_ROOT 환경변수) < company config < CLI --set.
    환경변수가 없으면 아무것도 바꾸지 않는다. secret 은 이 경로로 들어오지 않는다.
    """
    env = os.environ if environ is None else environ
    paths: dict[str, Any] = {}
    install_root = (env.get("DIA_INSTALL_ROOT") or "").strip()
    data_root = (env.get("DIA_DATA_ROOT") or "").strip()
    if install_root:
        paths["install_root"] = str(Path(install_root).expanduser().resolve())
        paths["company_root"] = str((Path(install_root).expanduser() / "company").resolve())
        if not data_root:
            data_root = str(Path(install_root).expanduser() / "workspace")
    if data_root:
        paths["data_root"] = str(Path(data_root).expanduser().resolve())
    return {"paths": paths} if paths else {}


def _resolve_relative_paths(cfg: dict[str, Any], base: Path) -> dict[str, Any]:
    paths = cfg.get("paths")
    if not isinstance(paths, dict):
        return cfg
    out = copy.deepcopy(cfg)
    for key in ("install_root", "data_root", "company_root", "output_root"):
        v = paths.get(key)
        if isinstance(v, str) and v and not Path(v).is_absolute():
            out["paths"][key] = str((base / v).resolve())
    for key in ("input_roots", "sources_roots"):
        vals = paths.get(key)
        if isinstance(vals, list):
            out["paths"][key] = [
                str((base / v).resolve()) if isinstance(v, str) and not Path(v).is_absolute() else v
                for v in vals
            ]
    return out


def load_config(
    config_path: str | None = None, overrides: dict[str, str] | None = None, *, profile: str | None = None
) -> AppConfig:
    """CLI 진입점용. overrides 는 'a.b.c=value' 형태의 dict[str,str]."""
    ov: dict[str, Any] = {}
    for k, v in (overrides or {}).items():
        ov[k] = _coerce_cli_value(v) if isinstance(v, str) else v
    if profile:
        try:
            Profile(profile)
        except ValueError as exc:
            raise AgentError(
                "E_CONFIG_INVALID",
                f"알 수 없는 profile: {profile}",
                details={"allowed": [p.value for p in Profile]},
            ) from exc
        ov["profile"] = profile
    return ConfigLoader(config_path=config_path, overrides=ov).build()


def resolve_secret(ref: str | None) -> str:
    """secret 참조를 실제 값으로 해결. 값은 절대 로그/예외에 포함하지 않는다."""
    if not ref:
        raise AgentError("E_CONFIG_SECRET_MISSING", "secret_ref 가 비어 있습니다")
    if ref.startswith("env:"):
        name = ref[4:]
        val = os.environ.get(name)
        if not val:
            raise AgentError(
                "E_CONFIG_SECRET_MISSING",
                f"환경 변수 '{name}' 가 설정되어 있지 않습니다",
                details={"ref": ref},
            )
        return val
    if ref.startswith("file:"):
        p = Path(ref[5:])
        if not p.is_file():
            raise AgentError(
                "E_CONFIG_SECRET_MISSING", "secret 파일이 없습니다", details={"ref": "file:<경로 생략>"}
            )
        return p.read_text(encoding="utf-8").strip()
    raise AgentError(
        "E_CONFIG_SECRET_MISSING", "지원되지 않는 secret 참조 형식", details={"ref": ref[:8] + "..."}
    )


def mask_config(obj: Any) -> Any:
    """resolved config 출력용: secret 관련 키의 값을 마스킹."""
    if isinstance(obj, dict):
        return {
            k: (MASK if any(s in k.lower() for s in SECRET_KEYS) and v not in (None, "") else mask_config(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [mask_config(x) for x in obj]
    return obj
