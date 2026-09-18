"""wheelhouse 재현 도구 (개인 PC 용, 표준 라이브러리 + pip).

반입 ZIP 을 다시 만들어야 할 때(예: ZIP 파일을 직접 전달받지 못한 경우) PyPI 에서 같은 wheel 을 내려받고
requirements/download_provenance.json 에 기록된 sha256 과 대조한다. 하나라도 다르면 실패한다.

사용 (Windows PowerShell, 프로젝트 루트에서):
  .\\.venv\\Scripts\\python.exe tools\\reproduce_wheelhouse.py --profile win-x64-cp312-cpu
  .\\.venv\\Scripts\\python.exe -m build --wheel
  .\\.venv\\Scripts\\python.exe -m corp_dl_agent package build --profile win-x64-cp312-cpu --wheelhouse wheelhouse\\win-x64-cp312-cpu --app-wheel dist\\corp_dl_agent-4.0.0-py3-none-any.whl --evidence-dir test-evidence --out dist
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

PROFILES = {
    "win-x64-cp312-cpu": {
        "resolved": "requirements/win-x64-cp312-cpu.resolved.txt",
        "pip_args": [
            "--platform",
            "win_amd64",
            "--python-version",
            "3.12",
            "--implementation",
            "cp",
            "--abi",
            "cp312",
            "--abi",
            "abi3",
            "--abi",
            "none",
        ],
    },
    "linux-x64-cp312-cpu": {
        "resolved": "requirements/profile-cpu-offline.in",
        "pip_args": [],  # 호스트 native (Linux x86_64 CPython 3.12 에서만)
    },
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True, choices=sorted(PROFILES))
    ap.add_argument("--wheelhouse", default=None)
    ap.add_argument("--provenance", default="requirements/download_provenance.json")
    ap.add_argument("--index-url", default=None, help="사내 승인 미러가 있으면 지정 (기본: pip 기본 index)")
    args = ap.parse_args()
    root = Path(__file__).resolve().parents[1]
    prof = PROFILES[args.profile]
    wh = Path(args.wheelhouse or (root / "wheelhouse" / args.profile))
    wh.mkdir(parents=True, exist_ok=True)
    prov = json.loads((root / args.provenance).read_text(encoding="utf-8"))
    expected = {w["filename"]: w["sha256"] for w in prov["profiles"][args.profile]["wheels"]}
    existing = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in wh.glob("*.whl")}
    if expected and all(existing.get(n) == h for n, h in expected.items()):
        print(
            f"PASS: wheelhouse 가 이미 provenance 와 일치합니다 ({len(expected)}개). 다운로드를 건너뜁니다."
        )
        return 0
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "download",
        "--no-cache-dir",
        "--disable-pip-version-check",
        "--only-binary=:all:",
        "-d",
        str(wh),
        "-r",
        str(root / prof["resolved"]),
        *prof["pip_args"],
    ]
    if prof["pip_args"]:
        cmd.append("--no-deps")
    if args.index_url:
        cmd += ["--index-url", args.index_url]
    print("$", " ".join(cmd))
    rc = subprocess.call(cmd)
    if rc != 0:
        print("pip download 실패", file=sys.stderr)
        return rc
    actual = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in wh.glob("*.whl")}
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    mismatch = sorted(n for n in expected if n in actual and actual[n] != expected[n])
    print(
        f"wheel {len(actual)}개, 기대 {len(expected)}개; 누락 {missing}; 추가 {extra}; hash 불일치 {mismatch}"
    )
    if missing or mismatch:
        print(
            "FAIL: provenance 와 다릅니다. 같은 버전의 wheel 이 PyPI 에서 바뀌었거나 다운로드가 손상되었습니다.",
            file=sys.stderr,
        )
        return 5
    print("PASS: wheelhouse 가 download_provenance.json 과 일치합니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
