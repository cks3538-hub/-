"""validate 명령: TaskSpec 데이터 계약·인코딩·고정 그룹 분할·누수 검사만 수행한다 (학습 없음).

validate --config <taskspec.yaml|json> [--app-config <company.yaml>] [--profile P] [--set k=v] [--output-dir DIR] [--lock-hash H] [--json]

산출물 (기본 <data_root>/outputs/validate/<task_id>/):
  data_report.json        인코딩/행·열/타깃 통계/issues (오류가 있어도 기록)
  split_manifest.json     고정 그룹 분할 (train/val/test ID, 그룹 수, 실제 행 비율, data/split/taskspec/code/lock hash)
  validation_summary.json PASS/FAIL 과 누수 검사·acceptance 상태 (acceptance 없으면 NEEDS_ACCEPTANCE_CRITERIA)
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from corp_dl_agent.cli import parse_overrides
from corp_dl_agent.common import Status, atomic_write_json, now_iso
from corp_dl_agent.config import AppConfig, load_config
from corp_dl_agent.errors import AgentError
from corp_dl_agent.security.paths import resolve_within
from corp_dl_agent.workspace import Workspace

SUMMARY_NAME = "validation_summary.json"
DATA_REPORT_NAME = "data_report.json"
SPLIT_MANIFEST_NAME = "split_manifest.json"


def register(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "validate",
        help="TaskSpec 데이터 계약·인코딩·고정 그룹 분할·누수 검사 (학습 없음)",
        description="TaskSpec 과 CSV 를 검사하여 data_report.json / split_manifest.json 을 만듭니다. 학습은 하지 않습니다.",
    )
    p.add_argument(
        "--config", dest="taskspec_path", required=True, help="TaskSpec YAML/JSON 경로 (strict schema)"
    )
    p.add_argument(
        "--app-config", dest="config_path", default=None, help="회사 설정 YAML 경로 (없으면 package defaults)"
    )
    p.add_argument(
        "--profile", default=None, help="personal-dev | transfer-test | corp-offline | corp-gateway"
    )
    p.add_argument(
        "--set",
        dest="overrides",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="설정 override (예: --set paths.data_root=workspace). 우선순위: defaults < app-config < --set",
    )
    p.add_argument("--json", dest="json_output", action="store_true", help="결과를 JSON 으로 출력")
    p.add_argument(
        "--output-dir", default=None, help="산출물 폴더 (기본: <data_root>/outputs/validate/<task_id>)"
    )
    p.add_argument(
        "--lock-hash",
        default=None,
        help="반입 lock 파일 hash. 없으면 'UNLOCKED' 로 기록합니다 (값을 만들어내지 않음)",
    )
    p.set_defaults(handler=run)


def input_roots(cfg: AppConfig, taskspec_path: Path) -> list[Path]:
    """데이터 파일 허용 root: 작업 폴더, workspace/company root, 설정의 input/sources roots, TaskSpec 이 있는 폴더."""
    ws = Workspace.from_config(cfg)
    roots: list[Path] = [Path.cwd().resolve(), ws.data_root, ws.company_root, taskspec_path.parent.resolve()]
    roots += [Path(p).expanduser().resolve() for p in cfg.paths.input_roots]
    roots += [Path(p).expanduser().resolve() for p in cfg.paths.sources_roots]
    return roots


def output_roots(cfg: AppConfig) -> list[Path]:
    ws = Workspace.from_config(cfg)
    roots: list[Path] = [ws.data_root, ws.outputs, Path.cwd().resolve()]
    if cfg.paths.output_root:
        roots.append(Path(cfg.paths.output_root).expanduser().resolve())
    return roots


def resolve_output_dir(cfg: AppConfig, requested: str | None, task_id: str) -> Path:
    ws = Workspace.from_config(cfg)
    if requested is None:
        out = ws.outputs / "validate" / task_id
        out.mkdir(parents=True, exist_ok=True)
        return out
    p = Path(requested).expanduser()
    parent = p.parent if str(p.parent) not in ("", ".") else Path.cwd()
    parent.mkdir(parents=True, exist_ok=True)
    resolved_parent = resolve_within(parent, output_roots(cfg), forbid_links=cfg.security.forbid_symlinks)
    out = resolved_parent / p.name
    out.mkdir(parents=True, exist_ok=True)
    return out


def _emit(args: argparse.Namespace, payload: dict[str, Any], lines: list[str]) -> None:
    if getattr(args, "json_output", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        for line in lines:
            print(line)


def _write_summary(out_dir: Path, summary: dict[str, Any]) -> Path:
    path = out_dir / SUMMARY_NAME
    atomic_write_json(path, summary)
    return path


def run(args: argparse.Namespace) -> int:
    from corp_dl_agent.ml import compute_code_hash, normalize_lock_hash
    from corp_dl_agent.ml.data import load_dataset
    from corp_dl_agent.ml.evaluation import acceptance_status
    from corp_dl_agent.ml.preprocessing import check_leakage, fit_preprocessor
    from corp_dl_agent.ml.split import apply_split, group_split, save_split
    from corp_dl_agent.ml.taskspec import NEEDS_ACCEPTANCE_CRITERIA, load_taskspec, taskspec_hash

    cfg = load_config(args.config_path, parse_overrides(args.overrides), profile=args.profile)
    ts_path = Path(args.taskspec_path).expanduser().resolve()
    spec = load_taskspec(ts_path)
    out_dir = resolve_output_dir(cfg, args.output_dir, spec.task_id)
    summary: dict[str, Any] = {
        "task_id": spec.task_id,
        "task_type": spec.task_type,
        "taskspec_path": str(ts_path),
        "taskspec_hash": taskspec_hash(spec),
        "data_origin": spec.data_origin,
        "synthetic": spec.data_origin == "synthetic",
        "training_performed": False,
        "lock_hash": normalize_lock_hash(args.lock_hash),
        "created_at": now_iso(),
        "status": Status.NOT_RUN.value,
        "checks": {},
    }

    # 1) 데이터 검증 (오류가 있어도 data_report.json 은 남긴다)
    try:
        df, report = load_dataset(
            spec, roots=input_roots(cfg, ts_path), forbid_links=cfg.security.forbid_symlinks
        )
    except AgentError as exc:
        rep = exc.details.pop("report", None)
        if isinstance(rep, dict):
            atomic_write_json(out_dir / DATA_REPORT_NAME, rep)
            exc.details["data_report_path"] = str(out_dir / DATA_REPORT_NAME)
        summary["status"] = Status.FAIL.value
        summary["checks"]["data"] = {"status": Status.FAIL.value, "error": exc.to_dict()}
        summary["summary_path"] = str(_write_summary(out_dir, summary))
        exc.details["summary_path"] = summary["summary_path"]
        raise
    atomic_write_json(out_dir / DATA_REPORT_NAME, report.model_dump(mode="json"))
    summary["checks"]["data"] = {
        "status": Status.PASS.value,
        "encoding_detected": report.encoding_detected,
        "n_rows": report.n_rows,
        "n_cols": report.n_cols,
        "n_groups": report.n_groups,
        "sha256": report.sha256,
        "warnings": [i.model_dump(mode="json") for i in report.issues if i.level != "error"],
        "target_stats": report.target_stats,
    }

    # 2) 고정 그룹 분할
    try:
        code_hash = compute_code_hash()
        manifest = group_split(
            df, spec, code_hash=code_hash, lock_hash=args.lock_hash, data_hash=report.sha256
        )
    except AgentError as exc:
        summary["status"] = Status.FAIL.value
        summary["checks"]["split"] = {"status": Status.FAIL.value, "error": exc.to_dict()}
        exc.details["summary_path"] = str(_write_summary(out_dir, summary))
        raise
    save_split(manifest, out_dir / SPLIT_MANIFEST_NAME)
    summary["checks"]["split"] = {
        "status": Status.PASS.value,
        "policy": manifest.policy,
        "n_groups": manifest.n_groups,
        "n_rows": manifest.n_rows,
        "row_fractions": manifest.row_fractions,
        "ratios_requested_groups": manifest.ratios_requested,
        "overlap_checked": manifest.overlap_checked,
        "time_boundaries": manifest.time_boundaries,
        "hashes": manifest.hashes,
    }

    # 3) 누수 검사 + train-only 전처리 fit (모델 학습 없음)
    train, val, test = apply_split(df, manifest)
    findings = check_leakage(train, spec)
    if findings:
        summary["status"] = Status.FAIL.value
        summary["checks"]["leakage"] = {
            "status": Status.FAIL.value,
            "findings": [f.model_dump(mode="json") for f in findings],
        }
        path = _write_summary(out_dir, summary)
        raise AgentError(
            "E_LEAKAGE",
            "ID 성격의 열이 feature 에 포함되어 있습니다: " + ", ".join(f.column for f in findings),
            details={"findings": [f.model_dump(mode="json") for f in findings], "summary_path": str(path)},
        )
    fp = fit_preprocessor(spec, train)
    _, val_tr = fp.transform_with_report(val)
    _, test_tr = fp.transform_with_report(test)
    summary["checks"]["leakage"] = {
        "status": Status.PASS.value,
        "findings": [],
        "excluded_columns": list(spec.excluded_columns),
    }
    summary["checks"]["preprocessing"] = {
        "status": Status.PASS.value,
        "fit_on": "train_only",
        "n_train_rows": fp.n_train_rows,
        "n_features_out": fp.n_features,
        "unknown_categories_in_val": val_tr.unknown_categories,
        "unknown_categories_in_test": test_tr.unknown_categories,
    }

    # 4) acceptance (임의 생성 금지)
    acc = acceptance_status(spec.acceptance, {})
    summary["acceptance"] = {
        "defined": spec.acceptance is not None,
        "state": NEEDS_ACCEPTANCE_CRITERIA if spec.acceptance is None else "DEFINED",
        "spec": spec.acceptance.model_dump(mode="json") if spec.acceptance is not None else None,
        "message": acc.message if spec.acceptance is None else "학습 후 final_evaluation 에서 판정합니다",
    }
    summary["status"] = Status.PASS.value
    summary["outputs"] = {
        "data_report": str(out_dir / DATA_REPORT_NAME),
        "split_manifest": str(out_dir / SPLIT_MANIFEST_NAME),
    }
    summary["summary_path"] = str(_write_summary(out_dir, summary))

    lines = [
        f"검증 완료 (학습 없음): {spec.task_id} [{spec.task_type}] synthetic={summary['synthetic']}",
        f"  데이터: {report.n_rows}행 × {report.n_cols}열, 인코딩 {report.encoding_detected}, 그룹 {report.n_groups}개, sha256 {report.sha256[:12]}…",
        f"  분할({manifest.policy}): 그룹 {manifest.n_groups} / 행 {manifest.n_rows} / 실제 행 비율 "
        + ", ".join(f"{k} {v:.3f}" for k, v in manifest.row_fractions.items())
        + " (요청 그룹 비율 0.70/0.15/0.15)",
        f"  누수 검사: 통과 (excluded_columns={list(spec.excluded_columns)}), 전처리 train-only fit → feature {fp.n_features}개",
        f"  lock hash: {manifest.hashes['lock']}",
        f"  acceptance: {summary['acceptance']['state']}",
        f"  산출물: {out_dir}",
    ]
    warn = summary["checks"]["data"]["warnings"]
    if warn:
        lines.append(f"  경고 {len(warn)}건: " + "; ".join(f"{w['code']}" for w in warn))
    _emit(args, {"ok": True, **summary}, lines)
    return 0
