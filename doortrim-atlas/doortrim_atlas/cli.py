"""명령줄 인터페이스.  사용법:  python -m doortrim_atlas <명령> [옵션]"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np

from .config import Config


def _log_factory(logfile=None):
    def log(msg=""):
        print(msg, flush=True)
        if logfile:
            logfile.write(str(msg) + "\n")
            logfile.flush()
    return log


def cmd_inspect_db(args):
    from .dbtools import inspect_db, format_schema, guess_sql
    schema = inspect_db(args.db)
    print(format_schema(schema))
    print("\n--- 자동 추측 SQL (틀리면 make-manifest --sql 로 직접 지정) ---")
    try:
        print(guess_sql(schema))
    except Exception as e:  # noqa: BLE001
        print(f"[추측 실패] {e}")


def cmd_make_manifest(args):
    from .dbtools import make_manifest
    sql = None
    if args.sql:
        sql = open(args.sql, "r", encoding="utf-8").read() if os.path.exists(args.sql) else args.sql
    make_manifest(args.db, args.out, sql=sql, stl_root=args.stl_root)


def cmd_build_atlas(args):
    from .atlas import Library, build_atlas, read_manifest
    cfg = Config.load(args.config)
    log = _log_factory()
    groups = read_manifest(args.manifest)
    if args.atlas_ids:
        wanted = set(args.atlas_ids.split(","))
        groups = {k: v for k, v in groups.items() if k in wanted}
    if args.max_atlases:
        groups = dict(list(groups.items())[: args.max_atlases])
    if args.append and os.path.exists(os.path.join(args.out, "library.json")):
        lib = Library.load(args.out)
        log(f"기존 라이브러리 불러옴: 아틀라스 {len(lib)}개")
    else:
        lib = Library(args.out)
    t0 = time.time()
    for i, (aid, bodies) in enumerate(groups.items()):
        if args.append and aid in lib._by_id and not args.rebuild:
            log(f"[{i+1}/{len(groups)}] {aid}: 이미 있음, 건너뜀")
            continue
        log(f"[{i+1}/{len(groups)}] {aid}: 바디 {len(bodies)}개")
        try:
            build_atlas(aid, bodies, lib, cfg, log=log)
        except Exception as e:  # noqa: BLE001
            log(f"  [오류] {aid}: {e}")
            if args.strict:
                raise
        if (i + 1) % 5 == 0:
            lib.save()
    lib.meta.update({"config": cfg.to_dict(), "built_at": time.strftime("%Y-%m-%d %H:%M:%S")})
    lib.save()
    log(f"\n라이브러리 저장: {args.out}  (아틀라스 {len(lib)}개, 코드 {len(lib.codes)}종, {time.time()-t0:.0f}초)")
    if args.preview:
        from .visualize import label, preview_png
        os.makedirs(os.path.join(args.out, "previews"), exist_ok=True)
        for a in lib.atlases[: args.preview]:
            preview_png(a.points, a.labels, lib.codes, os.path.join(args.out, "previews", f"{a.atlas_id}.png"),
                        title=label("아틀라스", "atlas") + f" {a.atlas_id}")
        log(f"미리보기 PNG {min(args.preview, len(lib))}장: {os.path.join(args.out, 'previews')}  ← 암레스트가 보이고 위아래가 맞는지 눈으로 확인하세요")


def cmd_train(args):
    from .atlas import Library
    from .classifier import save_classifier, train_classifier
    cfg = Config.load(args.config)
    lib = Library.load(args.library)
    bundle = train_classifier(lib, cfg)
    out = args.out or os.path.join(args.library, "classifier.pkl")
    save_classifier(bundle, out)
    print(f"분류기 저장: {out}")


def cmd_segment(args):
    from .atlas import Library
    from .classifier import load_classifier
    from .predict import load_cost_table, run_prediction
    from .segment import segment_mesh_files
    cfg = Config.load(args.config)
    lib = Library.load(args.library)
    model_path = args.model or os.path.join(args.library, "classifier.pkl")
    bundle = load_classifier(model_path) if (os.path.exists(model_path) and not args.no_clf) else None
    if bundle is None:
        print("[안내] 분류기 없이(아틀라스 전이만) 실행합니다. `train` 명령으로 분류기를 만들면 정확도가 올라갑니다.")
    os.makedirs(args.out, exist_ok=True)
    logfile = open(os.path.join(args.out, "log.txt"), "w", encoding="utf-8")
    log = _log_factory(logfile)
    t0 = time.time()
    summary = segment_mesh_files(args.skin, lib, bundle, cfg, args.out, log=log)
    if not args.no_predict:
        run_prediction(summary, lib, cfg, args.out, cost_table=load_cost_table(args.cost_table), log=log)
    log(f"\n완료 ({time.time()-t0:.0f}초). 결과 폴더: {args.out}")
    log("  preview.png          분할 결과 그림(정면/깊이)")
    log("  skin_labeled.ply     색상 입힌 메쉬(Windows 3D 뷰어/MeshLab에서 열기)")
    log("  parts/<코드>.stl     부품별로 잘라낸 메쉬")
    log("  parts.csv            부품별 면적·치수·신뢰도")
    log("  segmentation.json    전체 상세(좌표계, 유사 차종, 부품 통계)")
    log("  prediction.csv/.md   중량·재질·원가 예측")
    logfile.close()


def cmd_evaluate(args):
    from .atlas import Library
    from .evaluate import evaluate_library
    cfg = Config.load(args.config)
    lib = Library.load(args.library)
    out = args.out or os.path.join(args.library, "evaluation")
    evaluate_library(lib, cfg, out, max_folds=args.max_folds, use_clf=not args.no_clf, preview=not args.no_preview)


def cmd_synth(args):
    from .synthetic import generate_dataset
    m = generate_dataset(args.out, n_vehicles=args.n, seed=args.seed)
    print(f"합성 데이터 생성: {args.out}  (manifest: {m}, DB: {os.path.join(args.out, 'synthetic.db')})")


def cmd_demo(args):
    """합성 데이터로 전체 파이프라인을 끝까지 실행한다(설치 확인용)."""
    from .atlas import Library, build_atlas, read_manifest
    from .classifier import save_classifier, train_classifier
    from .evaluate import evaluate_library
    from .predict import run_prediction
    from .segment import segment_mesh_files
    from .synthetic import generate_dataset, make_skin_from_vehicle
    cfg = Config.load(args.config)
    root = args.out
    os.makedirs(root, exist_ok=True)
    log = _log_factory()
    t0 = time.time()
    log(f"[demo 1/5] 합성 차종 {args.n}대 생성")
    manifest = generate_dataset(os.path.join(root, "synthetic"), n_vehicles=args.n, seed=cfg.seed)
    groups = read_manifest(manifest)
    holdout = list(groups.keys())[-1]
    log(f"[demo 2/5] 아틀라스 구축 ({holdout} 는 신규 스킨 역할로 제외)")
    lib = Library(os.path.join(root, "library"))
    for aid, bodies in groups.items():
        if aid == holdout:
            continue
        log(f"  - {aid}")
        build_atlas(aid, bodies, lib, cfg, log=lambda m: None)
    lib.save()
    log(f"[demo 3/5] 분류기 학습")
    bundle = train_classifier(lib, cfg, log=log)
    save_classifier(bundle, os.path.join(root, "library", "classifier.pkl"))
    log(f"[demo 4/5] leave-one-out 평가 (폴드 {args.folds}개)")
    evaluate_library(lib, cfg, os.path.join(root, "evaluation"), max_folds=args.folds, log=lambda m: None, preview=False)
    with open(os.path.join(root, "evaluation", "evaluation_summary.json"), encoding="utf-8") as f:
        ev = json.load(f)
    log(f"  → 점 정확도 {ev['mean_point_accuracy']:.3f}, 부품 IoU 평균 {ev['mean_iou_over_parts']:.3f}")
    log(f"[demo 5/5] 신규 스킨({holdout}) 분할 + 예측")
    skin_path, truth = make_skin_from_vehicle(os.path.join(root, "synthetic", holdout), os.path.join(root, f"{holdout}_skin.stl"))
    out = os.path.join(root, "result_" + holdout)
    summary = segment_mesh_files(skin_path, lib, bundle, cfg, out, log=lambda m: None)
    pred = run_prediction(summary, lib, cfg, out, log=lambda m: None)
    log("  코드                 예측 보이는면적(cm²)  정답 윗면면적(cm²,≈전체/2)   예측중량(g)   정답중량(g)   오차")
    truth_w = {b["code"]: b["weight_g"] for b in groups[holdout]}
    errs = []
    for code, s in sorted(summary["parts"].items(), key=lambda kv: -kv[1]["area_visible_mm2"]):
        pw = pred["visible_parts"].get(code, {}).get("weight_g")
        tw = truth_w.get(code)
        err = f"{abs(pw - tw) / tw:6.0%}" if (pw and tw) else "   -"
        if pw and tw:
            errs.append(abs(pw - tw) / tw)
        log(f"  {code:20s} {s['area_visible_mm2']/100:16.0f} {truth.get(code, 0)/200:22.0f} {('%.0f' % pw) if pw else '-':>14s} {tw or 0:>12.0f} {err}")
    if errs:
        log(f"  중량 오차 중앙값 {np.median(errs):.0%} (사내 기존 방식 17~28%)")
    log(f"\n데모 완료 ({time.time()-t0:.0f}초). 결과: {out}/preview.png, prediction_summary.md")


def main(argv=None):
    p = argparse.ArgumentParser(prog="doortrim_atlas", description="도어트림 스킨 자동 부품분할·예측 (아틀라스 라벨 전이)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("inspect-db", help="사내 SQLite DB 스키마 출력 + manifest 용 SQL 추측")
    s.add_argument("--db", required=True)
    s.set_defaults(fn=cmd_inspect_db)

    s = sub.add_parser("make-manifest", help="DB → manifest.csv")
    s.add_argument("--db", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--sql", help="직접 작성한 SQL 문자열 또는 .sql 파일 경로")
    s.add_argument("--stl-root", help="stl_path 가 상대경로일 때 붙일 루트 폴더")
    s.set_defaults(fn=cmd_make_manifest)

    s = sub.add_parser("build-atlas", help="manifest.csv 의 STL 들로 아틀라스 라이브러리 구축")
    s.add_argument("--manifest", required=True)
    s.add_argument("--out", required=True, help="라이브러리 폴더")
    s.add_argument("--config")
    s.add_argument("--atlas-ids", help="쉼표로 구분한 일부 아틀라스만")
    s.add_argument("--max-atlases", type=int)
    s.add_argument("--append", action="store_true", help="기존 라이브러리에 추가")
    s.add_argument("--rebuild", action="store_true", help="--append 시 이미 있는 것도 다시 구축")
    s.add_argument("--strict", action="store_true", help="오류 시 중단")
    s.add_argument("--preview", type=int, default=6, help="미리보기 PNG 장수(0이면 생략)")
    s.set_defaults(fn=cmd_build_atlas)

    s = sub.add_parser("train", help="점 분류기 학습")
    s.add_argument("--library", required=True)
    s.add_argument("--out")
    s.add_argument("--config")
    s.set_defaults(fn=cmd_train)

    s = sub.add_parser("segment", help="신규 스킨 분할 + 예측")
    s.add_argument("--library", required=True)
    s.add_argument("--skin", required=True, help="스킨 STL 파일 또는 조각 STL 폴더")
    s.add_argument("--out", required=True)
    s.add_argument("--model", help="분류기 pkl (기본: 라이브러리/classifier.pkl)")
    s.add_argument("--config")
    s.add_argument("--cost-table", help="재질코드,kg당단가 CSV")
    s.add_argument("--no-clf", action="store_true")
    s.add_argument("--no-predict", action="store_true")
    s.set_defaults(fn=cmd_segment)

    s = sub.add_parser("evaluate", help="leave-one-vehicle-out 정확도 평가")
    s.add_argument("--library", required=True)
    s.add_argument("--out")
    s.add_argument("--config")
    s.add_argument("--max-folds", type=int)
    s.add_argument("--no-clf", action="store_true")
    s.add_argument("--no-preview", action="store_true")
    s.set_defaults(fn=cmd_evaluate)

    s = sub.add_parser("synth", help="합성 테스트 데이터 생성")
    s.add_argument("--out", required=True)
    s.add_argument("--n", type=int, default=20)
    s.add_argument("--seed", type=int, default=0)
    s.set_defaults(fn=cmd_synth)

    s = sub.add_parser("demo", help="합성 데이터로 전체 파이프라인 실행(설치 확인)")
    s.add_argument("--out", default="demo_out")
    s.add_argument("--n", type=int, default=12)
    s.add_argument("--folds", type=int, default=3)
    s.add_argument("--config")
    s.set_defaults(fn=cmd_demo)

    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
