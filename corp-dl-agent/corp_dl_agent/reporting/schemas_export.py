"""pydantic 모델 → schemas/*.schema.json 내보내기. 모델이 없는 모듈은 건너뛰고 목록을 보고한다."""

from __future__ import annotations

import importlib
from pathlib import Path

from corp_dl_agent.common import atomic_write_json
from corp_dl_agent.config.schemas import AppConfig

# (파일 이름, 모듈, 클래스)
_TARGETS: list[tuple[str, str, str]] = [
    ("config.schema.json", "corp_dl_agent.config.schemas", "AppConfig"),
    ("taskspec.schema.json", "corp_dl_agent.ml.taskspec", "TaskSpec"),
    ("cad_snapshot.schema.json", "corp_dl_agent.engineering.cad_snapshot", "CadSnapshot"),
    ("report_payload.schema.json", "corp_dl_agent.documents.payload", "ReportPayload"),
    ("release_manifest.schema.json", "corp_dl_agent.packaging.manifest", "ReleaseManifest"),
    ("target_profile.schema.json", "corp_dl_agent.packaging.target", "TargetProfile"),
    ("model_card.schema.json", "corp_dl_agent.reporting.model_card", "ModelCard"),
]


def export_all(schemas_dir: str | Path) -> dict[str, str]:
    """반환: {파일명: 'written'|'skipped: 사유'}"""
    out: dict[str, str] = {}
    d = Path(schemas_dir)
    d.mkdir(parents=True, exist_ok=True)
    for fname, mod_name, cls_name in _TARGETS:
        try:
            mod = importlib.import_module(mod_name)
            cls = getattr(mod, cls_name)
        except (ImportError, AttributeError) as exc:
            out[fname] = f"skipped: {type(exc).__name__}: {exc}"
            continue
        schema = cls.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["title"] = cls_name
        atomic_write_json(d / fname, schema)
        out[fname] = "written"
    assert isinstance(AppConfig, type)
    return out
