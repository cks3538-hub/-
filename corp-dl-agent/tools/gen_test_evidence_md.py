"""test-evidence/*.json -> docs/TEST_EVIDENCE.md 표 생성 (표준 라이브러리만)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--evidence-dir", default="test-evidence")
    ap.add_argument("--out", default="docs/TEST_EVIDENCE.md")
    args = ap.parse_args()
    recs = []
    for p in sorted(Path(args.evidence_dir).glob("*.json")):
        try:
            recs.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            print(f"skip {p.name}: {exc}")
    lines = [
        "# TEST_EVIDENCE",
        "",
        "개인 빌드 환경에서 실제 실행한 시험 목록. 경로는 `<PROJECT_ROOT>`/`<TEST_ROOT>`/`<HOME>` 으로 정규화했고, 원시 로그(개인 절대 경로 포함)는 `test-evidence/raw/` 에 로컬 보관하며 반입 ZIP 에 포함하지 않습니다. 실패/미실행은 그대로 기록합니다.",
        "",
        "| 이름 | 상태 | exit | 소요(s) | 환경 | 명령 |",
        "|---|---|---|---|---|---|",
    ]
    for r in recs:
        env = r.get("environment", {})
        env_s = f"{env.get('os', '')} {env.get('machine', '')} / {env.get('command_python') or env.get('collector_python') or ''}"
        if env.get("network_isolation") and "no isolation" not in str(env.get("network_isolation")):
            env_s += f" / {env['network_isolation']}"
        if env.get("user"):
            env_s += f" / {env['user']}"
        cmd = str(r.get("command", "")).replace("|", "\\|")
        if len(cmd) > 140:
            cmd = cmd[:137] + "..."
        lines.append(
            f"| {r.get('name')} | {r.get('status')} | {r.get('exit_code')} | {r.get('elapsed_seconds')} | {env_s} | `{cmd}` |"
        )
    lines += ["", "각 항목의 stdout/stderr 꼬리와 lock hash 는 `test-evidence/<이름>.json` 에 있습니다."]
    Path(args.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(recs)} records -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
