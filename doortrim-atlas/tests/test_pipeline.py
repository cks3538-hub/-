"""빠른 회귀 테스트: 합성 데이터 6대로 전체 파이프라인을 돌려 큰 부품 IoU 가 기준 이상인지 확인한다.
실행:  python -m pytest tests -q   또는   python tests/test_pipeline.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_end_to_end():
    from doortrim_atlas.atlas import Library, build_atlas, read_manifest
    from doortrim_atlas.classifier import train_classifier
    from doortrim_atlas.config import Config
    from doortrim_atlas.evaluate import evaluate_library
    from doortrim_atlas.predict import run_prediction
    from doortrim_atlas.segment import segment_mesh_files
    from doortrim_atlas.synthetic import generate_dataset, make_skin_from_vehicle

    cfg = Config()
    cfg.raw_samples, cfg.atlas_points, cfg.skin_samples, cfg.skin_points = 120_000, 15_000, 100_000, 20_000
    cfg.clf_trees = 80
    with tempfile.TemporaryDirectory() as d:
        manifest = generate_dataset(os.path.join(d, "syn"), n_vehicles=7, seed=1)
        groups = read_manifest(manifest)
        hold = list(groups)[-1]
        lib = Library(os.path.join(d, "lib"))
        for aid, bodies in groups.items():
            if aid != hold:
                build_atlas(aid, bodies, lib, cfg, log=lambda m: None)
        lib.save()
        assert len(lib) == 6 and len(lib.codes) == 9
        lib2 = Library.load(os.path.join(d, "lib"))
        assert len(lib2) == 6
        bundle = train_classifier(lib2, cfg, log=lambda m: None)
        ev = evaluate_library(lib2, cfg, os.path.join(d, "ev"), max_folds=2, log=lambda m: None, preview=False)
        assert ev["mean_point_accuracy"] > 0.8, ev
        big = [ev["by_code"][c]["mean_iou"] for c in ("MAIN_TRIM", "UPR_TRIM", "ARMREST", "MAP_POCKET") if c in ev["by_code"]]
        assert min(big) > 0.6, ev["by_code"]
        skin, truth = make_skin_from_vehicle(os.path.join(d, "syn", hold), os.path.join(d, "skin.stl"))
        out = os.path.join(d, "res")
        summ = segment_mesh_files(skin, lib2, bundle, cfg, out, log=lambda m: None)
        pred = run_prediction(summ, lib2, cfg, out, log=lambda m: None)
        for f in ("preview.png", "skin_labeled.ply", "parts.csv", "segmentation.json", "prediction.csv", "prediction_summary.md"):
            assert os.path.exists(os.path.join(out, f)), f
        assert "MAIN_TRIM" in summ["parts"] and "ARMREST" in summ["parts"]
        w = pred["visible_parts"]["ARMREST"]["weight_g"]
        assert w is not None and 50 < w < 2000
        with open(os.path.join(out, "segmentation.json"), encoding="utf-8") as f:
            json.load(f)


if __name__ == "__main__":
    test_end_to_end()
    print("OK")
