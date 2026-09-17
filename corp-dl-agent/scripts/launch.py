"""launch: active.json 의 venv Python 으로 `python -m corp_dl_agent <인자...>` 를 실행한다 (Activate 불필요).

사용: python launch.py [--install-root DIR] [--data-root DIR] [--] <corp_dl_agent 인자...>
  예: python launch.py -- demo --offline --device cpu --output-dir "D:/설계 자동화/workspace/outputs/demo"
- <install_root>/company/config/company_config.yaml 이 있고 사용자가 --config 를 주지 않았으면 설정을 받는 명령에
  `--config <그 경로>` 를 자동으로 덧붙인다 (version/validate/run/package 등 설정을 받지 않거나 --config 의미가 다른 명령은 제외).
- 환경변수 DIA_INSTALL_ROOT/DIA_DATA_ROOT 를 자식 프로세스에 전달한다. 사용자 환경(secret env 참조 등)은 그대로 둔다.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as c  # noqa: E402

CONFIG_COMMANDS = frozenset(
    {
        "doctor",
        "config",
        "self-test",
        "demo",
        "cad",
        "design",
        "docs",
        "integrations",
        "menu",
        "upgrade",
        "rollback",
    }
)
# package build/verify 는 회사 설정을 받지 않으므로 --config 를 붙이지 않는다.
COMPANY_CONFIG_REL = Path("company") / "config" / "company_config.yaml"


def split_args(argv: list[str]) -> tuple[Optional[str], Optional[str], list[str]]:
    """launch 자체 옵션(--install-root/--data-root)과 앱 인자를 분리한다. '--' 이후는 모두 앱 인자."""
    install_root: Optional[str] = None
    data_root: Optional[str] = None
    rest: list[str] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--":
            rest.extend(argv[i + 1 :])
            break
        if a == "--install-root" and i + 1 < len(argv):
            install_root = argv[i + 1]
            i += 2
            continue
        if a.startswith("--install-root="):
            install_root = a.split("=", 1)[1]
            i += 1
            continue
        if a == "--data-root" and i + 1 < len(argv):
            data_root = argv[i + 1]
            i += 2
            continue
        if a.startswith("--data-root="):
            data_root = a.split("=", 1)[1]
            i += 1
            continue
        rest.extend(argv[i:])
        break
    return install_root, data_root, rest


def resolve_active(install_root: Path) -> dict[str, Any]:
    active = c.read_active(install_root)
    if not active:
        raise c.ScriptError(
            "E_BLOCKED_CONFIG",
            f"설치된 release 가 없습니다 (active.json 없음): {install_root}",
            hint="02_Install.cmd (scripts/install.py) 로 먼저 설치하세요. 다른 설치 폴더면 DIA_INSTALL_ROOT 또는 --install-root 를 지정하세요.",
        )
    venv_py = Path(str(active.get("venv_python", "")))
    if not venv_py.is_file():
        raise c.ScriptError(
            "E_BLOCKED_CONFIG",
            f"active.json 의 venv python 이 없습니다: {venv_py}",
            hint="rollback 으로 다른 release 를 활성화하거나 다시 설치하세요.",
        )
    return active


def build_command(active: dict[str, Any], install_root: Path, app_args: list[str]) -> list[str]:
    app_module = str(active.get("app_module") or "corp_dl_agent")
    cmd = [str(active["venv_python"]), "-m", app_module, *app_args]
    company_cfg = install_root / COMPANY_CONFIG_REL
    if app_args and app_args[0] in CONFIG_COMMANDS and company_cfg.is_file():
        if not any(a == "--config" or a.startswith("--config=") for a in app_args):
            cmd += ["--config", str(company_cfg)]
    return cmd


def child_env(install_root: Path, data_root: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["DIA_INSTALL_ROOT"] = str(install_root)
    env["DIA_DATA_ROOT"] = str(data_root)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    return env


USAGE_KO = (
    "사용: python launch.py [--install-root DIR] [--data-root DIR] [--] <corp_dl_agent 인자...>\n"
    "  active.json 의 venv Python 으로 `python -m corp_dl_agent <인자...>` 를 실행합니다 (Activate 불필요).\n"
    "  인자가 없으면 menu 를 실행합니다. 앱 자체의 --help 는 `launch.py -- --help` 로 전달합니다.\n"
    "  환경변수: DIA_INSTALL_ROOT, DIA_DATA_ROOT\n"
)


def main(argv: Optional[list[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("-h", "--help"):
        print(USAGE_KO, end="")
        return c.EXIT_OK
    ir, dr, app_args = split_args(argv)
    install_root = Path(ir).expanduser() if ir else c.default_install_root()
    try:
        active = resolve_active(install_root)
    except c.ScriptError as exc:
        print(exc.format_ko(), file=sys.stderr)
        return exc.exit_code
    data_root = (
        Path(dr).expanduser()
        if dr
        else Path(str(active.get("data_root") or c.default_data_root(install_root)))
    )
    if not app_args:
        app_args = ["menu"]
    cmd = build_command(active, install_root, app_args)
    try:
        proc = subprocess.run(cmd, env=child_env(install_root, data_root))
    except OSError as exc:
        print(f"오류 E_INTERNAL: 앱 실행 실패: {exc}", file=sys.stderr)
        return c.EXIT_RUNTIME
    return int(proc.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
