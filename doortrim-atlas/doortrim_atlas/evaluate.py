"""Leave-one-vehicle-out 평가: 아틀라스 1건을 빼고 그 차종의 '보이는 점구름'을 신규 스킨처럼 취급해
분할 정확도(부품별 IoU)를 잰다. 사내 보고서의 SAM2 평균 IoU 50.5% 와 직접 비교할 수 있는 숫자다."""
from __future__ import annotations

import csv
import json
import os
import time
from typing import Dict, List, Optional

import numpy as np
from scipy.spatial import cKDTree
from scipy.spatial.transform import Rotation

from .classifier import train_classifier
from .segment import segment_points


def iou_per_code(pred: np.ndarray, truth: np.ndarray, n_codes: int) -> Dict[int, float]:
    out = {}
    for c in np.unique(truth):
        p, t = pred == c, truth == c
        inter, union = (p & t).sum(), (p | t).sum()
        out[int(c)] = float(inter / union) if union else 0.0
    return out


def evaluate_library(library, cfg, out_dir: str, max_folds: Optional[int] = None, use_clf: bool = True,
                     random_pose: bool = True, log=print, preview: bool = True) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    ids = [a.atlas_id for a in library.atlases]
    if max_folds:
        ids = ids[:max_folds]
    rng = np.random.default_rng(cfg.seed + 1)
    rows = []
    fold_summaries = []
    t0 = time.time()
    for k, aid in enumerate(ids):
        a = library.get(aid)
        lib = library.without(aid)
        log(f"\n===== fold {k+1}/{len(ids)}: {aid} 제외 =====")
        bundle = train_classifier(lib, cfg, log=log) if use_clf else None
        # 저장된 가시점(정규화)을 mm 로 되돌리고 임의 자세로 돌려 '모르는 좌표계'를 흉내낸다
        P_mm = a.frame.denormalize(a.points)
        N_mm = a.normals @ a.frame.R  # 역회전
        if random_pose:
            Rm = Rotation.random(random_state=int(rng.integers(0, 2**31 - 1))).as_matrix()
            t = rng.uniform(-1500, 1500, size=3)
            P_mm = P_mm @ Rm.T + t
            N_mm = N_mm @ Rm.T
        res = segment_points(P_mm, N_mm, lib, bundle, cfg, log=log)
        used = res["used_idx"]
        truth_used = a.labels[used]
        pred = res["labels"]
        # 사용되지 않은 점(가시성 필터로 제외 등)은 가장 가까운 사용 점의 라벨
        tree = cKDTree(P_mm[used])
        _, j = tree.query(P_mm, k=1)
        pred_all = pred[j]
        ious = iou_per_code(pred_all, a.labels, len(library.codes))
        acc = float((pred_all == a.labels).mean())
        miou = float(np.mean(list(ious.values())))
        fold_summaries.append({"atlas_id": aid, "point_accuracy": acc, "mean_iou": miou,
                               "pose_hypothesis": res["pose_detail"].get("hypothesis"), "mirrored": res["frame"].mirrored,
                               "top_atlas": res["ranking"][0][0] if res["ranking"] else None})
        log(f"  → 점 정확도 {acc:.3f}, 평균 IoU {miou:.3f}")
        for c, v in sorted(ious.items(), key=lambda kv: -kv[1]):
            n_pts = int((a.labels == c).sum())
            rows.append({"atlas_id": aid, "code": library.codes[c], "iou": v, "n_points": n_pts,
                         "visible_fraction": a.parts.get(library.codes[c], {}).get("visible_fraction")})
            log(f"      {library.codes[c]:20s} IoU {v:.3f}  (점 {n_pts})")
        if preview:
            from .visualize import label, preview_png
            preview_png(res["P_norm"], pred, library.codes, os.path.join(out_dir, f"fold_{aid}.png"),
                        title=label("LOO 평가", "leave-one-out") + f": {aid} (mIoU {miou:.2f})", truth=truth_used)
    with open(os.path.join(out_dir, "evaluation.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["atlas_id", "code", "iou", "n_points", "visible_fraction"])
        w.writeheader()
        w.writerows(rows)
    # 코드별 평균
    by_code: Dict[str, List[float]] = {}
    for r in rows:
        by_code.setdefault(r["code"], []).append(r["iou"])
    code_summary = {c: {"mean_iou": float(np.mean(v)), "n": len(v)} for c, v in by_code.items()}
    summary = {"n_folds": len(ids), "mean_point_accuracy": float(np.mean([f["point_accuracy"] for f in fold_summaries])),
               "mean_iou_over_parts": float(np.mean([r["iou"] for r in rows])),
               "mean_iou_over_folds": float(np.mean([f["mean_iou"] for f in fold_summaries])),
               "folds": fold_summaries, "by_code": code_summary, "elapsed_s": time.time() - t0, "config": cfg.to_dict()}
    with open(os.path.join(out_dir, "evaluation_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    log("\n================ 평가 요약 ================")
    log(f"폴드 {len(ids)}개 | 점 정확도 평균 {summary['mean_point_accuracy']:.3f} | 부품 IoU 평균 {summary['mean_iou_over_parts']:.3f}")
    for c, s in sorted(code_summary.items(), key=lambda kv: -kv[1]["mean_iou"]):
        log(f"  {c:20s} 평균 IoU {s['mean_iou']:.3f} (n={s['n']})")
    log(f"결과 파일: {out_dir}/evaluation.csv, evaluation_summary.json")
    return summary
