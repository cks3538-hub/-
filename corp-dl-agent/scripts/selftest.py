"""selftest: 설치된 release 의 venv Python 으로 `-m corp_dl_agent self-test` 를 실행한다 (launch 와 같은 방식).

사용: python selftest.py [--install-root DIR] [--data-root DIR] [--json] [-- 추가 인자...]
결과 JSON 은 <data_root>/logs/selftest_<시각>.json 에도 저장한다 (앱이 --output 을 지원할 때).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as c  # noqa: E402
import launch  # noqa: E402

USAGE_KO = (
    "사용: python selftest.py [--install-root DIR] [--data-root DIR] [--json] [-- 추가 인자...]\n"
    "  설치된 release 의 venv Python 으로 `python -m corp_dl_agent self-test` 를 실행하고 결과를 <data_root>/logs/ 에 저장합니다.\n"
)


def main(argv: Optional[list[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("-h", "--help"):
        print(USAGE_KO, end="")
        return c.EXIT_OK
    ir, dr, rest = launch.split_args(argv)
    want_json = "--json" in rest
    rest = [a for a in rest if a != "--json"]
    install_root = Path(ir).expanduser() if ir else c.default_install_root()
    try:
        active = launch.resolve_active(install_root)
    except c.ScriptError as exc:
        print(exc.format_ko(), file=sys.stderr)
        return exc.exit_code
    data_root = (
        Path(dr).expanduser()
        if dr
        else Path(str(active.get("data_root") or c.default_data_root(install_root)))
    )
    out_dir = data_root / "logs"
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"selftest_{c.timestamp_slug()}.json"
    except OSError:
        out_path = None
    app_args = ["self-test"]
    if want_json:
        app_args.append("--json")
    if out_path is not None:
        app_args += ["--output", str(out_path)]
    app_args += rest
    cmd = launch.build_command(active, install_root, app_args)
    proc = subprocess.run(cmd, env=launch.child_env(install_root, data_root))
    if not want_json and out_path is not None:
        print(f"self-test 결과 저장: {out_path}")
    return int(proc.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
