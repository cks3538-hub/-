from __future__ import annotations

import json
from pathlib import Path

from corp_dl_agent.reporting.model_card import SYNTHETIC_NOTICE, build_model_card, write_model_card
from corp_dl_agent.reporting.schemas_export import export_all


def test_model_card_synthetic_not_auto_selected(tmp_path: Path) -> None:
    card = build_model_card(
        {
            "run_id": "r1",
            "task": {
                "task_id": "t",
                "task_type": "regression",
                "target": "f",
                "units": {"f": "N"},
                "numeric_features": ["a"],
                "data_origin": "synthetic",
            },
            "selected": "ridge",
            "selected_kind": "sklearn",
            "final_evaluation": {"mae": 1.0},
            "train_ranges": {"a": {"min": 0.0, "max": 1.0}},
        }
    )
    assert card.production_auto_select is False
    assert SYNTHETIC_NOTICE in card.notices
    assert card.acceptance["state"] == "NEEDS_ACCEPTANCE_CRITERIA"
    p = write_model_card(card, tmp_path / "model_card.json", tmp_path / "model_card.md")
    d = json.loads(p.read_text(encoding="utf-8"))
    assert d["data_origin"] == "synthetic" and d["unit"] == "N"
    md = (tmp_path / "model_card.md").read_text(encoding="utf-8")
    assert "합성" in md and "NEEDS_ACCEPTANCE_CRITERIA" in md


def test_model_card_corporate_with_acceptance() -> None:
    card = build_model_card(
        {
            "run_id": "r",
            "task": {"task_id": "t", "task_type": "regression", "target": "f", "data_origin": "corporate"},
            "acceptance": {"state": "PASS", "metric": "mae"},
        }
    )
    assert card.production_auto_select is True and not card.notices


def test_schema_export(tmp_path: Path) -> None:
    res = export_all(tmp_path)
    assert res["config.schema.json"] == "written"
    schema = json.loads((tmp_path / "config.schema.json").read_text(encoding="utf-8"))
    assert schema["title"] == "AppConfig" and "properties" in schema
    assert schema.get("additionalProperties") is False
