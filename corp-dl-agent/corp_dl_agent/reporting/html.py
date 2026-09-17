"""report_ko.html / report_ko.md 렌더러.

- 외부 CDN/폰트/스크립트/이미지 참조 0. 인라인 CSS 만.
- 모든 값은 html.escape 로 escape. 숫자는 전달된 값만 표시하며 생성하지 않는다.
- 입력은 dict(run_summary). 알 수 없는 키는 무시하고, 없는 항목은 '없음' 으로 표시한다.
"""

from __future__ import annotations

import html
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from corp_dl_agent.common import atomic_write_text, now_iso
from corp_dl_agent.version import __version__

_CSS = """
body{font-family:'Malgun Gothic','Apple SD Gothic Neo','Noto Sans CJK KR',sans-serif;margin:24px;color:#1a1a1a;background:#fff}
h1{font-size:1.5em;border-bottom:2px solid #333;padding-bottom:4px}
h2{font-size:1.15em;margin-top:1.4em;border-left:4px solid #555;padding-left:8px}
table{border-collapse:collapse;margin:8px 0;font-size:0.95em}
th,td{border:1px solid #bbb;padding:4px 8px;text-align:left;vertical-align:top}
th{background:#eee}
.badge{display:inline-block;padding:1px 6px;border-radius:3px;font-size:0.85em;border:1px solid #999}
.warn{color:#8a3b00}
.muted{color:#666;font-size:0.9em}
code{background:#f3f3f3;padding:1px 3px}
"""


def _e(v: Any) -> str:
    if v is None:
        return "없음"
    if isinstance(v, bool):
        return "예" if v else "아니오"
    if isinstance(v, float):
        return html.escape(f"{v:.6g}")
    return html.escape(str(v))


def _table(headers: Iterable[str], rows: Iterable[Iterable[Any]]) -> str:
    h = "".join(f"<th>{_e(x)}</th>" for x in headers)
    body = "".join("<tr>" + "".join(f"<td>{_e(c)}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><thead><tr>{h}</tr></thead><tbody>{body}</tbody></table>"


def _kv_table(d: Mapping[str, Any]) -> str:
    return _table(["항목", "값"], [(k, _fmt_value(v)) for k, v in d.items()])


def _fmt_value(v: Any) -> Any:
    if isinstance(v, dict):
        return ", ".join(f"{k}={_fmt_value(x)}" for k, x in v.items())
    if isinstance(v, list | tuple):
        return ", ".join(str(_fmt_value(x)) for x in v)
    return v


def _metrics_rows(cands: Iterable[Mapping[str, Any]]) -> tuple[list[str], list[list[Any]]]:
    cands = list(cands)
    keys: list[str] = []
    for c in cands:
        for k in c.get("metrics") or {}:
            if k not in keys and not isinstance((c.get("metrics") or {})[k], dict | list):
                keys.append(k)
    rows = []
    for c in cands:
        m = c.get("metrics") or {}
        rows.append([c.get("name"), c.get("kind"), c.get("status"), *[m.get(k) for k in keys]])
    return ["후보", "종류", "상태", *keys], rows


def render_report_ko(
    summary: Mapping[str, Any], out_html: str | Path, out_md: str | Path | None = None
) -> Path:
    """run summary dict → report_ko.html (+ report_ko.md). 반환: html 경로."""
    parts: list[str] = []
    title = f"학습 보고서 — {summary.get('task_id') or summary.get('run_id') or ''}"
    parts.append(
        f"<!DOCTYPE html><html lang='ko'><head><meta charset='utf-8'><title>{_e(title)}</title><style>{_CSS}</style></head><body>"
    )
    parts.append(f"<h1>{_e(title)}</h1>")
    parts.append(
        f"<p class='muted'>run_id: <code>{_e(summary.get('run_id'))}</code> · 상태: <span class='badge'>{_e(summary.get('status'))}</span>"
        f" · data_origin: <span class='badge'>{_e(summary.get('data_origin'))}</span> · 생성: {_e(summary.get('generated_at') or now_iso())} · corp-dl-agent {_e(__version__)}</p>"
    )
    if summary.get("data_origin") == "synthetic":
        parts.append(
            "<p class='warn'>이 결과는 합성(synthetic) 데이터로 만든 것입니다. 업무 판단에 사용하지 마세요.</p>"
        )
    for w in summary.get("warnings") or []:
        parts.append(f"<p class='warn'>경고: {_e(w)}</p>")

    parts.append("<h2>과제</h2>")
    task = summary.get("task") or {}
    parts.append(
        _kv_table(
            {
                k: task.get(k)
                for k in ("task_id", "task_type", "target", "metric", "direction", "seed", "purpose")
                if k in task
            }
        )
    )

    parts.append("<h2>데이터</h2>")
    parts.append(_kv_table(summary.get("data_report") or {}))
    parts.append("<h2>분할</h2>")
    parts.append(_kv_table(summary.get("split") or {}))

    parts.append("<h2>후보 비교 (validation)</h2>")
    headers, rows = _metrics_rows(summary.get("candidates") or [])
    parts.append(_table(headers, rows) if rows else "<p>후보 없음</p>")
    parts.append(
        f"<p>선택 모델: <b>{_e(summary.get('selected'))}</b> (validation 기준 선택, test 는 1회 평가)</p>"
    )

    parts.append("<h2>최종 평가 (test, 1회)</h2>")
    fe = summary.get("final_evaluation") or {}
    parts.append(
        _kv_table({k: v for k, v in fe.items() if not isinstance(v, dict | list)}) if fe else "<p>없음</p>"
    )
    if isinstance(fe.get("confusion_matrix"), dict | list):
        parts.append(f"<p>혼동행렬: <code>{_e(fe.get('confusion_matrix'))}</code></p>")

    parts.append("<h2>업무 허용 기준</h2>")
    acc = summary.get("acceptance") or {}
    parts.append(
        _kv_table(acc) if acc else "<p>NEEDS_ACCEPTANCE_CRITERIA — 허용오차가 입력되지 않았습니다.</p>"
    )

    parts.append("<h2>환경·예산</h2>")
    parts.append(_kv_table(summary.get("environment") or {}))
    parts.append(_kv_table(summary.get("budget") or {}))

    parts.append("<h2>산출물</h2>")
    arts = summary.get("artifacts") or {}
    parts.append(_table(["이름", "경로/hash"], [(k, v) for k, v in arts.items()]) if arts else "<p>없음</p>")
    parts.append("</body></html>")
    out_html = Path(out_html)
    atomic_write_text(out_html, "\n".join(parts))
    if out_md is not None:
        atomic_write_text(Path(out_md), render_report_md(summary))
    return out_html


def render_report_md(summary: Mapping[str, Any]) -> str:
    def esc(v: Any) -> str:
        return str(_fmt_value(v)).replace("|", "\\|").replace("\n", " ") if v is not None else "없음"

    lines = [f"# 학습 보고서 — {esc(summary.get('task_id') or summary.get('run_id'))}", ""]
    lines.append(
        f"- run_id: `{esc(summary.get('run_id'))}` / 상태: {esc(summary.get('status'))} / data_origin: {esc(summary.get('data_origin'))}"
    )
    if summary.get("data_origin") == "synthetic":
        lines.append("- **합성 데이터 결과입니다. 업무 판단에 사용하지 마세요.**")
    for w in summary.get("warnings") or []:
        lines.append(f"- 경고: {esc(w)}")
    for title, key in (
        ("데이터", "data_report"),
        ("분할", "split"),
        ("환경", "environment"),
        ("예산", "budget"),
        ("업무 허용 기준", "acceptance"),
    ):
        lines += ["", f"## {title}", "", "| 항목 | 값 |", "|---|---|"]
        d = summary.get(key) or {}
        lines += [f"| {esc(k)} | {esc(v)} |" for k, v in d.items()] or ["| (없음) | |"]
    headers, rows = _metrics_rows(summary.get("candidates") or [])
    lines += ["", "## 후보 비교 (validation)", ""]
    if rows:
        lines.append("| " + " | ".join(esc(h) for h in headers) + " |")
        lines.append("|" + "---|" * len(headers))
        lines += ["| " + " | ".join(esc(c) for c in r) + " |" for r in rows]
    else:
        lines.append("(후보 없음)")
    lines += [
        "",
        f"선택 모델: **{esc(summary.get('selected'))}**",
        "",
        "## 최종 평가 (test, 1회)",
        "",
        "| 항목 | 값 |",
        "|---|---|",
    ]
    fe = summary.get("final_evaluation") or {}
    lines += [f"| {esc(k)} | {esc(v)} |" for k, v in fe.items()] or ["| (없음) | |"]
    return "\n".join(lines) + "\n"
