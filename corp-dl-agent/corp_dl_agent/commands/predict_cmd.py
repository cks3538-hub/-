"""predict 명령: export 번들로 CSV 예측.

predict --model <export dir> --input <csv> --output <csv> [--allow-synthetic] [--app-config <yaml>] [--profile P] [--set k=v] [--json]

- 신뢰된(이 프로그램이 만든, checksum 일치) 번들만 로드한다. 외부 임의 pickle/joblib 은 E_ARTIFACT_UNTRUSTED.
- synthetic 모델은 --allow-synthetic (또는 ml.allow_synthetic_models_in_production=true) 없이는 E_ARTIFACT_SYNTHETIC.
- 출력 CSV 옆에 <output>.manifest.json (모델/입력 hash, 행 수, 외삽 행 수, synthetic 표시) 를 남긴다.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from corp_dl_agent.cli import parse_overrides
from corp_dl_agent.config import load_config
from corp_dl_agent.errors import EXIT_OK


def register(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "predict",
        help="export 번들(sklearn/MLP)로 CSV 예측 (id/prediction/probability/label)",
        description="run 의 export 폴더(또는 복사한 번들)로 입력 CSV 를 예측합니다. checksum·생성 표시가 검증된 번들만 로드합니다.",
    )
    p.add_argument("--model", dest="model_dir", required=True, help="export 번들 폴더 (manifest.json 포함)")
    p.add_argument("--input", dest="input_csv", required=True, help="입력 CSV (UTF-8/UTF-8-SIG/CP949)")
    p.add_argument("--output", dest="output_csv", required=True, help="출력 CSV 경로 (utf-8-sig)")
    p.add_argument(
        "--allow-synthetic",
        dest="allow_synthetic",
        action="store_true",
        help="합성(synthetic) 데이터로 학습한 모델 허용 (운영 자동 채택 금지 — demo/검증 용도)",
    )
    p.add_argument(
        "--app-config", dest="config_path", default=None, help="회사 설정 YAML 경로 (없으면 package defaults)"
    )
    p.add_argument(
        "--profile", default=None, help="personal-dev | transfer-test | corp-offline | corp-gateway"
    )
    p.add_argument(
        "--set",
        dest="overrides",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="설정 override (예: --set paths.data_root=workspace)",
    )
    p.add_argument("--json", dest="json_output", action="store_true", help="결과를 JSON 으로 출력")
    p.set_defaults(handler=run)


def run(args: argparse.Namespace) -> int:
    from corp_dl_agent.ml.predict import predict_cli

    cfg = load_config(args.config_path, parse_overrides(args.overrides), profile=args.profile)
    result: dict[str, Any] = predict_cli(
        args.model_dir,
        args.input_csv,
        args.output_csv,
        cfg,
        allow_synthetic=True if args.allow_synthetic else None,
    )
    if args.json_output:
        print(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2, default=str))
    else:
        print(
            f"예측 완료: {result['n_rows']}행 → {result['output_csv']} "
            f"(모델 {result['model_name']} [{result['model_kind']}], run {result['run_id']}, data_origin={result['data_origin']})"
        )
        if result["synthetic"]:
            print("  주의: 합성(synthetic) 데이터로 학습한 모델입니다. 업무 판단에 사용하지 마세요.")
        if result["n_out_of_train_range"]:
            print(f"  학습 범위 밖(외삽) 행: {result['n_out_of_train_range']}개 (in_train_range 열 참조)")
        if result["imputed_values"]:
            print(f"  빈 값 대치(train 중앙값): {result['imputed_values']}")
        print(f"  provenance: {result['sidecar']}")
    return EXIT_OK
