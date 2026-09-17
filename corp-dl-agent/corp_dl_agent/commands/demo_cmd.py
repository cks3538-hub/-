"""demo 명령: 합성 fixture 로 전체 업무 흐름을 오프라인으로 실행한다.

demo --offline --device cpu [--output-dir DIR] [--fast] [--fixtures-dir DIR] [--json]
  CAD A/B 수입 → 중량/원가 비교 → 합성 회귀/분류 run → predict → 근거 검색 → report_payload
  → review.pptx / comparison.xlsx → demo_manifest.json (산출물 hash, 원본 fixture 불변, 문서 수치 일치, synthetic 표시)

- 인터넷/로그인/외부 서비스 없이 실행되며 OutboundGuard 가 프로세스 내 외부 연결 시도를 차단·기록한다.
- 원본 fixture 는 읽기 전용이다. 모든 산출물(run/상태 DB/색인 포함)은 --output-dir 아래에만 생긴다.
- 종료 코드: 모든 단계·검사 PASS 면 0, 아니면 실패 단계의 오류 코드(없으면 5). demo_manifest.json 은 항상 남긴다.
"""

from __future__ import annotations

import argparse
import json
import sys

from corp_dl_agent.cli import add_common_arguments, parse_overrides
from corp_dl_agent.config import load_config


def register(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "demo",
        help="합성 데이터 전체 흐름 demo (CAD A/B → 중량/원가 → 학습/예측 → 근거 검색 → PPTX/XLSX), 오프라인",
        description=(
            "합성 fixture 만으로 CAD A/B 수입 → 중량/원가 비교 → 회귀/분류 학습 → 예측 → 근거 검색 → "
            "같은 report_payload 로 PPTX/XLSX 생성까지 실행하고 demo_manifest.json 으로 묶습니다. "
            "인터넷/로그인이 필요 없으며 원본 fixture 는 수정하지 않습니다."
        ),
    )
    add_common_arguments(p)
    p.add_argument(
        "--offline",
        action="store_true",
        default=True,
        help="오프라인 실행 (demo 는 항상 오프라인이며 외부 연결 시도는 차단·기록됩니다)",
    )
    p.add_argument(
        "--device",
        choices=("cpu", "cuda"),
        default="cpu",
        help="학습 장치 (기본 cpu). cuda 는 CUDA 가 실제로 사용 가능할 때만 허용 (아니면 E_NOT_SUPPORTED)",
    )
    p.add_argument(
        "--output-dir",
        dest="output_dir",
        default=None,
        help="산출물 폴더 (기본 <data_root>/outputs/demo-<UTC시각>). 부모 폴더가 승인 root 안이어야 합니다",
    )
    p.add_argument(
        "--fast",
        action="store_true",
        help="빠른 확인용 예산 (MLP 후보 1, 2 epochs, 120초; 기본은 후보 2, 10 epochs, 300초)",
    )
    p.add_argument(
        "--fixtures-dir",
        dest="fixtures_dir",
        default=None,
        help="합성 fixture 폴더 (기본: 프로젝트 fixtures/ → 환경변수 DIA_FIXTURES_DIR → 활성 release 의 fixtures/)",
    )
    p.set_defaults(handler=run)


def run(args: argparse.Namespace) -> int:
    from corp_dl_agent.demo import format_demo_ko, run_demo

    overrides = parse_overrides(args.overrides)
    overrides["ml.device"] = args.device
    cfg = load_config(args.config_path, overrides, profile=args.profile)
    json_output = bool(getattr(args, "json_output", False))

    def progress(msg: str) -> None:
        # JSON 모드에서는 stdout 을 JSON 만으로 유지하기 위해 진행 상황을 stderr 로 보낸다.
        print(msg, file=sys.stderr if json_output else sys.stdout, flush=True)

    manifest = run_demo(
        cfg,
        offline=bool(args.offline),
        device=args.device,
        out_dir=args.output_dir,
        fast=bool(args.fast),
        fixtures_dir=args.fixtures_dir,
        progress=progress,
    )
    if json_output:
        print(
            json.dumps(
                {"ok": manifest.all_pass, **manifest.model_dump(mode="json")}, ensure_ascii=False, indent=2
            )
        )
    else:
        print(format_demo_ko(manifest))
    return int(manifest.exit_code)
