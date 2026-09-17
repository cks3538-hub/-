"""model_card.json / model_card.md.

data_origin(synthetic|corporate), 목적, metrics, acceptance 상태, 학습 범위, hash 를 기록한다.
합성 모델은 운영 자동 선택에서 제외된다는 문구를 항상 포함한다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from corp_dl_agent.common import StrictModel, atomic_write_json, atomic_write_text, now_iso
from corp_dl_agent.version import SCHEMA_VERSION, __version__

SYNTHETIC_NOTICE = "이 모델은 합성(synthetic) 데이터로 학습되었습니다. 사내 운영에서 자동 선택되지 않으며 업무 판단에 사용하지 마세요."


class ModelCard(StrictModel):
    schema_version: str = SCHEMA_VERSION
    generator: str = f"corp-dl-agent {__version__}"
    created_at: str
    run_id: str
    task_id: str
    task_type: str
    target: str
    unit: str | None = None
    data_origin: str
    purpose: str = ""
    selected_model: str
    model_kind: str
    features: dict[str, list[str]]
    train_ranges: dict[str, dict[str, float]] = {}
    split: dict[str, Any] = {}
    validation_metrics: dict[str, Any] = {}
    test_metrics: dict[str, Any] = {}
    acceptance: dict[str, Any] = {}
    threshold: float | None = None
    hashes: dict[str, str] = {}
    production_auto_select: bool
    notices: list[str] = []
    limitations: list[str] = []


def build_model_card(run: dict[str, Any]) -> ModelCard:
    """runner 가 만든 summary dict 로부터 model card 를 구성한다. 없는 항목은 비워 둔다."""
    task = run.get("task") or {}
    data_origin = str(run.get("data_origin") or task.get("data_origin") or "unknown")
    acc = run.get("acceptance") or {
        "state": "NEEDS_ACCEPTANCE_CRITERIA",
        "message": "업무 허용오차가 입력되지 않았습니다.",
    }
    notices: list[str] = []
    if data_origin == "synthetic":
        notices.append(SYNTHETIC_NOTICE)
    if acc.get("state") == "NEEDS_ACCEPTANCE_CRITERIA":
        notices.append("NEEDS_ACCEPTANCE_CRITERIA: 업무 허용오차가 없어 합격 여부를 판정하지 않았습니다.")
    limitations = list(run.get("limitations") or [])
    limitations.append("학습 범위(train_ranges) 밖의 입력에 대한 예측은 외삽이며 신뢰도가 낮습니다.")
    limitations.append("다른 플랫폼/버전에서 비트 단위 재현성은 보장하지 않습니다.")
    return ModelCard(
        created_at=now_iso(),
        run_id=str(run.get("run_id") or ""),
        task_id=str(task.get("task_id") or run.get("task_id") or ""),
        task_type=str(task.get("task_type") or ""),
        target=str(task.get("target") or ""),
        unit=(task.get("units") or {}).get(task.get("target") or "")
        if isinstance(task.get("units"), dict)
        else None,
        data_origin=data_origin,
        purpose=str(task.get("purpose") or run.get("purpose") or ""),
        selected_model=str(run.get("selected") or ""),
        model_kind=str(run.get("selected_kind") or ""),
        features={
            "numeric": list(task.get("numeric_features") or []),
            "categorical": list(task.get("categorical_features") or []),
            "excluded": list(task.get("excluded_columns") or []),
        },
        train_ranges=run.get("train_ranges") or {},
        split=run.get("split") or {},
        validation_metrics=run.get("validation_metrics") or {},
        test_metrics=run.get("final_evaluation") or {},
        acceptance=acc,
        threshold=run.get("threshold"),
        hashes=run.get("hashes") or {},
        production_auto_select=(data_origin == "corporate"),
        notices=notices,
        limitations=limitations,
    )


def write_model_card(card: ModelCard, out_json: str | Path, out_md: str | Path | None = None) -> Path:
    out_json = Path(out_json)
    atomic_write_json(out_json, card.model_dump(mode="json"))
    if out_md is not None:
        atomic_write_text(Path(out_md), model_card_md(card))
    return out_json


def model_card_md(card: ModelCard) -> str:
    def row(k: str, v: Any) -> str:
        return f"| {k} | {str(v).replace('|', '/')} |"

    lines = [f"# Model Card — {card.task_id} ({card.run_id})", ""]
    for n in card.notices:
        lines.append(f"> **{n}**")
    lines += ["", "| 항목 | 값 |", "|---|---|"]
    lines += [
        row("task_type", card.task_type),
        row("target", f"{card.target} ({card.unit or '단위 미지정'})"),
        row("data_origin", card.data_origin),
        row("purpose", card.purpose),
        row("selected_model", f"{card.selected_model} ({card.model_kind})"),
        row("threshold", card.threshold if card.threshold is not None else "해당 없음"),
        row("production_auto_select", card.production_auto_select),
        row("acceptance", card.acceptance),
        row("created_at", card.created_at),
    ]
    lines += [
        "",
        "## 특징량",
        "",
        f"- numeric: {', '.join(card.features.get('numeric', []))}",
        f"- categorical: {', '.join(card.features.get('categorical', []))}",
        f"- excluded: {', '.join(card.features.get('excluded', []))}",
    ]
    lines += ["", "## 학습 범위", "", "| 특징량 | min | max |", "|---|---|---|"]
    lines += [f"| {k} | {v.get('min')} | {v.get('max')} |" for k, v in card.train_ranges.items()] or [
        "| (없음) | | |"
    ]
    lines += ["", "## validation metrics", "", "| 항목 | 값 |", "|---|---|"] + [
        row(k, v) for k, v in card.validation_metrics.items() if not isinstance(v, dict | list)
    ]
    lines += ["", "## test metrics (1회)", "", "| 항목 | 값 |", "|---|---|"] + [
        row(k, v) for k, v in card.test_metrics.items() if not isinstance(v, dict | list)
    ]
    lines += ["", "## hashes", "", "| 항목 | 값 |", "|---|---|"] + [row(k, v) for k, v in card.hashes.items()]
    lines += ["", "## 한계", ""] + [f"- {x}" for x in card.limitations]
    return "\n".join(lines) + "\n"
