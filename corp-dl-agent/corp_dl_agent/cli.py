"""CLI 진입점. 각 명령은 corp_dl_agent.commands.<module> 에 있으며 register(subparsers) 로 등록한다.

규칙:
- 최상위에서 third-party 를 import 하지 않는다 (doctor/version 은 torch/pandas 없이 실행되어야 한다).
- 모든 명령 handler 는 int 종료 코드를 반환하며 AgentError 는 여기서 한국어로 출력한다.
- 비밀값은 어떤 경로로도 출력하지 않는다.
"""

from __future__ import annotations

import argparse
import importlib
import sys
import traceback
from collections.abc import Sequence

from corp_dl_agent.errors import EXIT_RUNTIME, EXIT_USAGE, AgentError
from corp_dl_agent.version import __version__

COMMAND_MODULES: tuple[str, ...] = (
    "corp_dl_agent.commands.version_cmd",
    "corp_dl_agent.commands.doctor_cmd",
    "corp_dl_agent.commands.config_cmd",
    "corp_dl_agent.commands.selftest_cmd",
    "corp_dl_agent.commands.demo_cmd",
    "corp_dl_agent.commands.validate_cmd",
    "corp_dl_agent.commands.run_cmd",
    "corp_dl_agent.commands.predict_cmd",
    "corp_dl_agent.commands.cad_cmd",
    "corp_dl_agent.commands.design_cmd",
    "corp_dl_agent.commands.docs_cmd",
    "corp_dl_agent.commands.integrations_cmd",
    "corp_dl_agent.commands.package_cmd",
    "corp_dl_agent.commands.upgrade_cmd",
    "corp_dl_agent.commands.menu_cmd",
)


class KoreanArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # type: ignore[override]
        self.print_usage(sys.stderr)
        sys.stderr.write(f"오류 E_USAGE: {message}\n해결 방법: 위 사용법 또는 --help 를 확인하세요.\n")
        raise SystemExit(EXIT_USAGE)


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    """모든 명령에 공통인 설정 관련 인자."""
    parser.add_argument(
        "--config", dest="config_path", default=None, help="회사 설정 YAML 경로 (없으면 package defaults)"
    )
    parser.add_argument(
        "--profile", default=None, help="personal-dev | transfer-test | corp-offline | corp-gateway"
    )
    parser.add_argument(
        "--set",
        dest="overrides",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="설정 override (예: --set ml.device=cpu). 우선순위: defaults < config < --set",
    )
    parser.add_argument("--json", dest="json_output", action="store_true", help="결과를 JSON 으로 출력")


def parse_overrides(pairs: Sequence[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in pairs:
        if "=" not in p:
            raise AgentError("E_USAGE", f"--set 형식은 KEY=VALUE 이어야 합니다: {p}")
        k, v = p.split("=", 1)
        out[k.strip()] = v
    return out


def build_parser() -> argparse.ArgumentParser:
    parser = KoreanArgumentParser(
        prog="corp-dl-agent",
        description="설계·문서 업무 자동화 범용 코어 (python -m corp_dl_agent)",
        epilog="처음이면 'menu' 또는 'doctor' 부터 실행하세요. 각 명령의 자세한 사용법: <명령> --help",
    )
    parser.add_argument("--version", action="version", version=f"corp-dl-agent {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<명령>")
    missing: list[str] = []
    for mod_name in COMMAND_MODULES:
        try:
            mod = importlib.import_module(mod_name)
        except ModuleNotFoundError as exc:
            if exc.name == mod_name:
                missing.append(mod_name)
                continue
            raise
        mod.register(sub)
    parser.set_defaults(_missing_command_modules=missing)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return EXIT_USAGE
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return EXIT_USAGE
    try:
        return int(handler(args))
    except AgentError as exc:
        if getattr(args, "json_output", False):
            import json

            print(json.dumps({"ok": False, "error": exc.to_dict()}, ensure_ascii=False, indent=2))
        else:
            print(exc.format_ko(), file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        print(
            "중단되었습니다 (KeyboardInterrupt). 실행 중이던 run 은 status 명령으로 확인하세요.",
            file=sys.stderr,
        )
        return EXIT_RUNTIME
    except Exception as exc:  # noqa: BLE001 - 최종 안전망
        print(f"오류 E_INTERNAL: 예기치 않은 오류: {type(exc).__name__}: {exc}", file=sys.stderr)
        if getattr(args, "debug", False):
            traceback.print_exc()
        print(
            "해결 방법: 재현 가능한 합성 입력과 함께 보고하세요. (--debug 로 상세 traceback)", file=sys.stderr
        )
        return EXIT_RUNTIME


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
