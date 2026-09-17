"""run / status / pause / resume / cancel / report 명령 (학습 run 수명주기).

run     --config <taskspec.yaml|json> [--app-config <company.yaml>] [--profile P] [--set k=v] [--run-id ID] [--resume]
        [--fast] [--lock-hash H] [--json]
status  --run-id ID [--json]           상태 DB 의 run/trial/attempt/lease + summary.json
pause   --run-id ID                    epoch/trial 경계에서 멈추도록 요청 (실행 중인 worker 가 다음 경계에서 PAUSED 로 전이)
resume  --run-id ID [--fast]           run 폴더의 taskspec.json 으로 이어서 실행 (코드/데이터/lock 이 다르면 E_FINGERPRINT_CHANGED)
cancel  --run-id ID                    cancel 요청 (worker 가 없으면 즉시 CANCELLED)
report  --run-id ID                    summary.json 으로 report_ko.md/html 재생성

규칙: --config 는 TaskSpec, --app-config 는 앱 설정 (validate 명령과 동일). 한국어 메시지, --json 출력 지원.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from corp_dl_agent.cli import parse_overrides
from corp_dl_agent.common import atomic_write_text
from corp_dl_agent.config import AppConfig, load_config
from corp_dl_agent.errors import EXIT_BUDGET, EXIT_OK, AgentError
from corp_dl_agent.workspace import Workspace

FAST_BUDGET: dict[str, Any] = {
    "wall_time_seconds": 120,
    "max_candidates": 1,
    "max_epochs": 2,
    "patience": 2,
    "max_calls": 0,
    "max_tokens": 0,
    "mode": "custom",
}


def _add_app_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--app-config", dest="config_path", default=None, help="회사 설정 YAML 경로 (없으면 package defaults)"
    )
    p.add_argument("--profile", default=None, help="personal-dev | transfer-test | corp-offline | corp-gateway")
    p.add_argument(
        "--set",
        dest="overrides",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="설정 override (예: --set paths.data_root=workspace, --set ml.device=cpu)",
    )
    p.add_argument("--json", dest="json_output", action="store_true", help="결과를 JSON 으로 출력")


def register(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "run",
        help="TaskSpec 학습 run (기준 모델 3종 + MLP 후보 → validation 선택 → test 1회 → export)",
        description="TaskSpec CSV 로 기준 모델과 MLP 후보를 학습하고 validation 으로 선택한 뒤 test 를 1회 평가합니다. "
        "산출물은 <data_root>/runs/<run_id>/ 에 저장되며 synthetic 표시를 남깁니다.",
    )
    p.add_argument("--config", dest="taskspec_path", required=True, help="TaskSpec YAML/JSON 경로 (strict schema)")
    _add_app_args(p)
    p.add_argument("--run-id", dest="run_id", default=None, help="run 식별자 (기본: <task_id>-<시각>-<난수>)")
    p.add_argument("--resume", action="store_true", help="같은 run_id 를 마지막 완료 epoch 뒤부터 이어서 실행")
    p.add_argument("--fast", action="store_true", help="빠른 확인용 예산 (후보 1, 2 epochs, 120초)")
    p.add_argument("--lock-hash", dest="lock_hash", default=None, help="반입 lock 파일 hash (없으면 'UNLOCKED')")
    p.set_defaults(handler=run_handler)

    for name, help_text in (
        ("status", "run 상태·trial·attempt·lease 표시"),
        ("pause", "epoch/trial 경계에서 멈추도록 pause 요청"),
        ("cancel", "cancel 요청 (worker 가 없으면 즉시 CANCELLED)"),
        ("report", "summary.json 으로 report_ko.md/html 재생성"),
    ):
        q = sub.add_parser(name, help=help_text, description=help_text)
        q.add_argument("--run-id", dest="run_id", required=True, help="run 식별자")
        _add_app_args(q)
        q.set_defaults(handler={"status": status_handler, "pause": pause_handler, "cancel": cancel_handler, "report": report_handler}[name])

    r = sub.add_parser(
        "resume",
        help="PAUSED/BUDGET_EXCEEDED/중단된 run 을 마지막 완료 epoch 뒤부터 이어서 실행",
        description="run 폴더의 taskspec.json 을 읽어 이어서 실행합니다. 코드/데이터/lock/설정이 달라졌으면 E_FINGERPRINT_CHANGED 로 거부합니다.",
    )
    r.add_argument("--run-id", dest="run_id", required=True, help="run 식별자")
    _add_app_args(r)
    r.add_argument("--fast", action="store_true", help="빠른 확인용 예산 (후보 1, 2 epochs, 120초) — 처음 run 과 같아야 재개됩니다")
    r.add_argument("--lock-hash", dest="lock_hash", default=None, help="반입 lock 파일 hash (처음 run 과 같아야 합니다)")
    r.set_defaults(handler=resume_handler)


def _cfg(args: argparse.Namespace) -> AppConfig:
    return load_config(args.config_path, parse_overrides(args.overrides), profile=args.profile)


def _emit(args: argparse.Namespace, payload: dict[str, Any], lines: list[str]) -> None:
    if getattr(args, "json_output", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        for line in lines:
            print(line)


def _summary_lines(summary: Any) -> list[str]:
    metric = summary.task.get("metric")
    lines = [
        f"run {summary.run_id} [{summary.task_id}/{summary.task_type}] 상태: {summary.status} synthetic={summary.synthetic}",
        f"  폴더: {summary.run_dir}",
    ]
    if summary.candidates:
        lines.append("  후보 (validation):")
        for c in summary.candidates:
            val = c.metrics.get(metric)
            val_s = f"{val:.6g}" if isinstance(val, (int, float)) else "없음"
            extra = f", epochs {c.epochs}" if c.epochs is not None else ""
            extra += f", reused {c.reused_from}" if c.reused_from else ""
            lines.append(f"    - {c.name} ({c.kind}) {c.status} {metric}={val_s}{extra}")
    if summary.selected:
        lines.append(f"  선택: {summary.selected} ({summary.selected_kind}) — validation 기준, test 1회 평가")
        fe = {k: v for k, v in summary.final_evaluation.items() if isinstance(v, (int, float))}
        lines.append("  test: " + ", ".join(f"{k}={v:.6g}" for k, v in fe.items()))
    lines.append(f"  acceptance: {summary.acceptance.get('state')} — {summary.acceptance.get('message', '')}")
    b = summary.budget or {}
    lines.append(f"  예산: {b.get('elapsed_seconds')}s / {b.get('wall_time_seconds')}s (mode {b.get('mode')}, 완료 보증 아님)")
    for w in summary.warnings:
        lines.append(f"  경고: {w}")
    if summary.message:
        lines.append(f"  {summary.message}")
    return lines


def _exit_code(status: str) -> int:
    return EXIT_BUDGET if status == "BUDGET_EXCEEDED" else EXIT_OK


def run_handler(args: argparse.Namespace) -> int:
    from corp_dl_agent.ml.runner import run_task
    from corp_dl_agent.ml.taskspec import load_taskspec

    cfg = _cfg(args)
    ws = Workspace.from_config(cfg)
    spec = load_taskspec(Path(args.taskspec_path).expanduser().resolve())
    summary = run_task(
        spec,
        cfg,
        ws,
        run_id=args.run_id,
        resume=bool(args.resume),
        budget_override=dict(FAST_BUDGET) if args.fast else None,
        lock_hash=args.lock_hash,
    )
    _emit(args, {"ok": summary.status == "COMPLETED", **summary.model_dump(mode="json")}, _summary_lines(summary))
    return _exit_code(summary.status)


def resume_handler(args: argparse.Namespace) -> int:
    from corp_dl_agent.ml.runner import load_run_taskspec, run_task

    cfg = _cfg(args)
    ws = Workspace.from_config(cfg)
    run_dir = ws.run_dir(args.run_id)
    if not run_dir.is_dir():
        raise AgentError("E_INPUT_INVALID", f"run 폴더가 없습니다: {args.run_id}", details={"run_dir": str(run_dir)})
    spec = load_run_taskspec(run_dir)
    summary = run_task(
        spec,
        cfg,
        ws,
        run_id=args.run_id,
        resume=True,
        budget_override=dict(FAST_BUDGET) if args.fast else None,
        lock_hash=args.lock_hash,
    )
    _emit(args, {"ok": summary.status == "COMPLETED", **summary.model_dump(mode="json")}, _summary_lines(summary))
    return _exit_code(summary.status)


def _open_coordinator(cfg: AppConfig) -> Any:
    from corp_dl_agent.state.coordinator import Coordinator
    from corp_dl_agent.state.db import StateDB

    ws = Workspace.from_config(cfg)
    if not ws.state_db.is_file():
        raise AgentError("E_INPUT_INVALID", "상태 DB 가 없습니다 (아직 run 을 실행한 적이 없습니다)", details={"path": str(ws.state_db)})
    db = StateDB(ws.state_db)
    return ws, db, Coordinator(db)


def status_handler(args: argparse.Namespace) -> int:
    from corp_dl_agent.ml.runner import load_summary

    cfg = _cfg(args)
    ws, db, coord = _open_coordinator(cfg)
    try:
        run = coord.get_run(args.run_id)
        lease = coord.get_lease(args.run_id)
        trials = coord.list_trials(args.run_id)
        payload: dict[str, Any] = {
            "ok": True,
            "run": run.model_dump(mode="json"),
            "lease": lease.model_dump(mode="json") if lease else None,
            "lease_valid": bool(lease and lease.is_valid(coord.now())),
            "trials": [
                {**t.model_dump(mode="json"), "attempts_detail": [a.model_dump(mode="json") for a in coord.list_attempts(t.trial_id)]}
                for t in trials
            ],
            "run_dir": str(ws.run_dir(args.run_id)),
        }
        summary = load_summary(ws.run_dir(args.run_id))
        if summary is not None:
            payload["summary"] = summary.model_dump(mode="json")
    finally:
        db.close()
    lines = [
        f"run {run.run_id} [{run.kind}] 상태: {run.status.value}"
        + (f" (이전: {run.previous_status.value})" if run.previous_status else ""),
        f"  pause_requested={run.pause_requested} cancel_requested={run.cancel_requested} worker={run.worker_id or '없음'}",
        f"  lease: {'유효' if payload['lease_valid'] else '없음/만료'}",
        f"  폴더: {payload['run_dir']}",
    ]
    if run.reason:
        lines.append(f"  사유: {run.reason}")
    for t in trials:
        lines.append(f"  - trial {t.trial_id}: {t.status.value} attempts={t.attempts}" + (f" ({t.reason})" if t.reason else ""))
    if summary is not None:
        lines += _summary_lines(summary)
    _emit(args, payload, lines)
    return EXIT_OK


def pause_handler(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    _, db, coord = _open_coordinator(cfg)
    try:
        run = coord.request_pause(args.run_id)
        lease = coord.get_lease(args.run_id)
        live = bool(lease and lease.is_valid(coord.now()))
    finally:
        db.close()
    msg = (
        "실행 중인 worker 가 다음 epoch/trial 경계에서 PAUSED 로 전이합니다."
        if live
        else "실행 중인 worker 가 없습니다. 다음 resume 는 요청을 지우고 이어서 실행합니다."
    )
    _emit(
        args,
        {"ok": True, "run_id": run.run_id, "status": run.status.value, "pause_requested": run.pause_requested, "worker_live": live},
        [f"pause 요청 기록: run {run.run_id} (현재 상태 {run.status.value}). {msg}"],
    )
    return EXIT_OK


def cancel_handler(args: argparse.Namespace) -> int:
    from corp_dl_agent.state.machine import RunStatus

    cfg = _cfg(args)
    _, db, coord = _open_coordinator(cfg)
    try:
        run = coord.request_cancel(args.run_id)
        lease = coord.get_lease(args.run_id)
        live = bool(lease and lease.is_valid(coord.now()))
        immediate = False
        if not live and run.status is not RunStatus.CANCELLED:
            run = coord.set_status(args.run_id, RunStatus.CANCELLED, "cancel 명령 (실행 중인 worker 없음)")
            immediate = True
    finally:
        db.close()
    _emit(
        args,
        {"ok": True, "run_id": run.run_id, "status": run.status.value, "cancel_requested": run.cancel_requested, "immediate": immediate},
        [
            f"cancel 요청 기록: run {run.run_id} → 상태 {run.status.value}"
            + ("" if immediate else " (실행 중인 worker 가 다음 경계에서 CANCELLED 로 전이합니다)")
        ],
    )
    return EXIT_OK


def report_handler(args: argparse.Namespace) -> int:
    from corp_dl_agent.ml.runner import load_summary

    cfg = _cfg(args)
    ws = Workspace.from_config(cfg)
    run_dir = ws.run_dir(args.run_id)
    summary = load_summary(run_dir)
    if summary is None:
        raise AgentError("E_INPUT_INVALID", f"summary.json 이 없어 보고서를 만들 수 없습니다: {args.run_id}", details={"run_dir": str(run_dir)})
    d = summary.model_dump(mode="json")
    out_md = run_dir / "report_ko.md"
    out_html = run_dir / "report_ko.html"
    html_status = "NOT_RUN"
    try:
        from corp_dl_agent.reporting.html import render_report_ko

        render_report_ko(d, out_html, out_md)
        html_status = "PASS"
    except ImportError:
        lines = [f"# 학습 보고서 — {summary.task_id}", "", *_summary_lines(summary)]
        atomic_write_text(out_md, "\n".join(lines) + "\n")
    _emit(
        args,
        {"ok": True, "run_id": summary.run_id, "report_md": str(out_md), "report_html": str(out_html) if html_status == "PASS" else None, "html_status": html_status},
        [f"보고서 생성: {out_md}" + (f", {out_html}" if html_status == "PASS" else " (html: reporting 모듈 없음 → NOT_RUN)")],
    )
    return EXIT_OK
