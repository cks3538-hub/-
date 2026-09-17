"""처음 시작 메뉴. 실제 구현된(모듈 import 가능한) 항목만 노출한다. 외부 로그인/웹 없음."""

from __future__ import annotations

import argparse
import importlib
from collections.abc import Callable

from corp_dl_agent.cli import add_common_arguments

# (번호, 표시명, 명령 모듈, 실행할 argv 생성)
MENU_ITEMS: list[tuple[str, str, str, list[str]]] = [
    (
        "1",
        "demo (합성 데이터 전체 흐름: CAD A/B → 중량/원가 → 학습/예측 → 문서 생성)",
        "corp_dl_agent.commands.demo_cmd",
        ["demo", "--offline", "--device", "cpu"],
    ),
    ("2", "설정 검사 (doctor)", "corp_dl_agent.commands.doctor_cmd", ["doctor"]),
    (
        "3",
        "설계 비교 (design compare --help)",
        "corp_dl_agent.commands.design_cmd",
        ["design", "compare", "--help"],
    ),
    ("4", "학습 (run --help)", "corp_dl_agent.commands.run_cmd", ["run", "--help"]),
    (
        "5",
        "문서 작성 (docs generate --help)",
        "corp_dl_agent.commands.docs_cmd",
        ["docs", "generate", "--help"],
    ),
    ("6", "상태 확인 (self-test)", "corp_dl_agent.commands.selftest_cmd", ["self-test"]),
]


def available_items() -> list[tuple[str, str, str, list[str]]]:
    out = []
    for item in MENU_ITEMS:
        try:
            importlib.import_module(item[2])
        except ModuleNotFoundError:
            continue
        out.append(item)
    return out


def register(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser("menu", help="처음 시작 메뉴 (구현된 기능만 표시)")
    add_common_arguments(p)
    p.add_argument("--choice", default=None, help="비대화형: 선택 번호 (예: --choice 2)")
    p.add_argument("--list", action="store_true", help="메뉴 항목만 출력하고 종료")
    p.set_defaults(handler=run)


def _render(items: list[tuple[str, str, str, list[str]]]) -> str:
    lines = ["설계·문서 업무 자동화 프로그램 — 메뉴 (인터넷/로그인 불필요)"]
    for num, label, _mod, _argv in items:
        lines.append(f"  {num}. {label}")
    lines.append("  0. 종료")
    return "\n".join(lines)


def run(args: argparse.Namespace, *, input_fn: Callable[[str], str] = input) -> int:
    items = available_items()
    print(_render(items))
    if args.list:
        return 0
    choice = args.choice
    if choice is None:
        try:
            choice = input_fn("번호를 입력하세요: ").strip()
        except (EOFError, KeyboardInterrupt):
            return 0
    if choice in ("0", ""):
        return 0
    for num, _label, _mod, argv in items:
        if num == choice:
            from corp_dl_agent.cli import main

            extra: list[str] = []
            if getattr(args, "config_path", None):
                extra += ["--config", args.config_path]
            if getattr(args, "profile", None):
                extra += ["--profile", args.profile]
            if "--help" in argv:
                extra = []
            return main(argv + extra)
    print(f"오류 E_USAGE: 알 수 없는 선택 '{choice}'. 위 번호 중 하나를 입력하세요.")
    return 2
