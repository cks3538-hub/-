from __future__ import annotations

import re
from pathlib import Path

from corp_dl_agent.reporting.html import render_report_ko, render_report_md

SUMMARY = {
    "run_id": "run-1",
    "task_id": "clip",
    "status": "COMPLETED",
    "data_origin": "synthetic",
    "task": {
        "task_id": "clip",
        "task_type": "regression",
        "target": "force",
        "metric": "mae",
        "direction": "min",
        "seed": 1,
    },
    "data_report": {"n_rows": 120, "encoding": "utf-8-sig", "note": "<script>alert(1)</script>"},
    "split": {"n_rows": {"train": 84, "val": 18, "test": 18}},
    "candidates": [
        {"name": "ridge", "kind": "sklearn", "status": "COMPLETED", "metrics": {"mae": 1.25, "rmse": 1.7}},
        {"name": "mlp-0", "kind": "mlp", "status": "COMPLETED", "metrics": {"mae": 1.1, "rmse": 1.5}},
    ],
    "selected": "mlp-0",
    "final_evaluation": {"mae": 1.2, "rmse": 1.6, "r2": 0.9, "p95_abs_error": 3.1},
    "acceptance": {},
    "environment": {"python": "3.12", "device": "cpu"},
    "budget": {"wall_time_seconds": 300},
    "artifacts": {"export": "export/"},
    "warnings": ["a & b < c"],
}


def test_render_html_escapes_and_has_no_external_refs(tmp_path: Path) -> None:
    out = render_report_ko(SUMMARY, tmp_path / "report_ko.html", tmp_path / "report_ko.md")
    html = out.read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    assert "a &amp; b &lt; c" in html
    assert not re.search(r"(src|href)=[\"']?https?://", html)
    assert "cdn" not in html.lower()
    assert "1.25" in html and "mlp-0" in html
    assert "NEEDS_ACCEPTANCE_CRITERIA" in html
    assert "합성(synthetic)" in html
    md = (tmp_path / "report_ko.md").read_text(encoding="utf-8")
    assert "mlp-0" in md and "| mae |" in md


def test_render_md_handles_missing_sections() -> None:
    md = render_report_md({"run_id": "r", "status": "FAILED"})
    assert "학습 보고서" in md and "(후보 없음)" in md
