"""시험 증거 수집기 (표준 라이브러리만).

사용: python tools/collect_evidence.py --name pytest-core --cwd <dir> --normalize <PROJECT_ROOT>=<path> [--normalize <TEST_ROOT>=<path>] -- <command...>
결과: test-evidence/<name>.json (정규화된 경로·명령·exit code·환경·stdout/stderr 꼬리·소요시간)
      test-evidence/raw/<name>.log (원시 로그, 개인 로컬 보관용 — 반입 ZIP 에 포함하지 않음)
사실을 바꾸지 않는다: exit code 와 실패 출력은 그대로 기록한다.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import time
from pathlib import Path


def normalize(text: str, mapping: list[tuple[str, str]]) -> str:
    out = text
    for placeholder, real in sorted(mapping, key=lambda kv: -len(kv[1])):
        if real:
            out = out.replace(real, placeholder)
            out = out.replace(real.replace("/", "\\"), placeholder)
    home = os.path.expanduser("~")
    if home and home != "/":
        out = out.replace(home, "<HOME>")
    # 다른 사용자 홈(예: 비관리자 시험 계정)도 일반화 — 반입 증거에 개인 절대 경로가 남지 않게
    out = re.sub(r"/home/[A-Za-z0-9._-]+", "<HOME>", out)
    out = re.sub(r"(?i)([A-Za-z]:\\+Users\\+)[^\\\s\"']+", r"\1<USER>", out)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--cwd", default=".")
    ap.add_argument("--out-dir", default="test-evidence")
    ap.add_argument("--normalize", action="append", default=[], help="<PLACEHOLDER>=/real/path")
    ap.add_argument("--tail", type=int, default=120)
    ap.add_argument("--lock", default=None, help="lock 파일 경로 (hash 기록)")
    ap.add_argument("--timeout", type=int, default=7200)
    ap.add_argument("--python-note", default=None, help="명령이 사용한 Python (예: 'CPython 3.12.3 (.venv)')")
    ap.add_argument(
        "--network-note",
        default="host network (no isolation)",
        help="예: 'unshare -n (no network namespace)'",
    )
    ap.add_argument("--user-note", default=None, help="예: 'non-root user tester'")
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    args = ap.parse_args()
    cmd = args.cmd[1:] if args.cmd and args.cmd[0] == "--" else args.cmd
    if not cmd:
        print("command required after --", file=sys.stderr)
        return 2
    mapping = []
    for n in args.normalize:
        ph, real = n.split("=", 1)
        mapping.append((ph, str(Path(real).resolve())))
    out_dir = Path(args.out_dir)
    raw_dir = out_dir / "raw"
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)
    started = dt.datetime.now(dt.UTC)
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            cwd=args.cwd,
            capture_output=True,
            text=True,
            timeout=args.timeout,
            encoding="utf-8",
            errors="replace",
        )
        code, stdout, stderr, timed_out = proc.returncode, proc.stdout, proc.stderr, False
    except subprocess.TimeoutExpired as exc:
        code, stdout, stderr, timed_out = (
            -1,
            (exc.stdout or "") if isinstance(exc.stdout, str) else "",
            (exc.stderr or "") if isinstance(exc.stderr, str) else "",
            True,
        )
    elapsed = round(time.monotonic() - t0, 2)
    raw_path = raw_dir / f"{args.name}.log"
    raw_path.write_text(
        f"$ {' '.join(cmd)}\n[exit {code}]\n--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}\n",
        encoding="utf-8",
    )
    lock_hash = None
    if args.lock and Path(args.lock).is_file():
        lock_hash = hashlib.sha256(Path(args.lock).read_bytes()).hexdigest()
    record = {
        "name": args.name,
        "command": normalize(" ".join(cmd), mapping),
        "cwd": normalize(str(Path(args.cwd).resolve()), mapping),
        "started_at": started.isoformat(timespec="seconds"),
        "elapsed_seconds": elapsed,
        "exit_code": code,
        "timed_out": timed_out,
        "status": "PASS" if code == 0 else "FAIL",
        "environment": {
            "os": platform.system(),
            "os_release": platform.release(),
            "machine": platform.machine(),
            "collector_python": platform.python_version(),
            "command_python": args.python_note,
            "network_isolation": args.network_note,
            "user": args.user_note,
        },
        "lock_sha256": lock_hash,
        "stdout_tail": normalize("\n".join(stdout.splitlines()[-args.tail :]), mapping),
        "stderr_tail": normalize("\n".join(stderr.splitlines()[-args.tail :]), mapping),
        "raw_log": f"test-evidence/raw/{args.name}.log (개인 로컬 보관, 반입 제외)",
    }
    (out_dir / f"{args.name}.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"[{record['status']}] {args.name} exit={code} elapsed={elapsed}s -> {out_dir / (args.name + '.json')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
