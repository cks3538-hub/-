"""신규 스킨 분할: 점구름 단계(핵심)와 메쉬 단계(파일 입출력·면 라벨링) 두 층으로 나뉜다."""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
from scipy.spatial import cKDTree

from .atlas import Library, choose_frame
from .classifier import predict_proba_full
from .features import compute_features, orient_normals
from .fuse import clean_components, fuse_probs, smooth_probs
from .geometry import list_mesh_files, load_mesh, random_subsample, sample_meshes
from . import transfer


def segment_points(P: np.ndarray, N: np.ndarray, library: Library, clf_bundle: Optional[dict], cfg,
                   log=print, exclude: Sequence[str] = ()) -> dict:
    """mm 좌표 점구름(P, 법선 N)을 받아 점마다 표준부품코드를 붙인다.

    반환 dict:
      frame, used_idx(입력 인덱스), P_norm, N_norm, labels, prob, ranking, transfer_info, pose_detail
    """
    rng = np.random.default_rng(cfg.seed)
    n_codes = len(library.codes)
    log("[1/6] 자세 정규화")
    frame, vis, N, pose_detail = choose_frame(P, N, library, cfg, log=log, exclude=exclude)
    if not cfg.skin_visibility:
        vis = np.ones(len(P), dtype=bool)
    vis_idx = np.flatnonzero(vis)
    used = vis_idx[random_subsample(len(vis_idx), cfg.skin_points, rng)]
    Pn = frame.normalize(P[used])
    Nn = orient_normals(frame.rotate_vec(N[used]))
    log(f"      가시점 {len(vis_idx):,}/{len(P):,} → 사용 {len(used):,}개, 거울상={frame.mirrored}")

    log("[2/6] 유사 아틀라스 순위")
    sub = Pn[random_subsample(len(Pn), 3000, rng)]
    ranking = transfer.rank_atlases(sub, library, exclude=exclude)
    for aid, d in ranking[: cfg.k_atlas]:
        log(f"      {aid}: {d:.4f}")

    log("[3/6] 아틀라스 라벨 전이(정렬 + 이웃 투표)")
    prob_atlas, info = transfer.transfer_labels(Pn, library, ranking, n_codes, cfg, log=log)

    log("[4/6] 점 분류기")
    F, _ = compute_features(Pn, Nn)
    prob_clf = predict_proba_full(clf_bundle, F, n_codes)
    if prob_clf is None:
        log("      (분류기 없음 → 아틀라스 확률만 사용)")

    log("[5/6] 융합 + 평활")
    prob = fuse_probs(prob_atlas, prob_clf, cfg.alpha_atlas, mode=cfg.fusion)
    if cfg.smooth_iters > 0:
        prob = smooth_probs(Pn, Nn, prob, k=cfg.smooth_k, iters=cfg.smooth_iters, lam=cfg.smooth_lambda)
    labels = prob.argmax(axis=1)

    log("[6/6] 작은 조각 정리")
    labels = clean_components(Pn, labels, k=cfg.smooth_k, min_frac=cfg.min_component_frac,
                              expected_frac=library.expected_label_fractions(exclude=exclude), size_coef=cfg.cleanup_size_coef)
    return {"frame": frame, "used_idx": used, "vis_mask": vis, "P_norm": Pn, "N_norm": Nn, "labels": labels, "prob": prob,
            "prob_atlas": prob_atlas, "prob_clf": prob_clf, "ranking": ranking, "transfer_info": info,
            "pose_detail": pose_detail, "features": F}


def faces_from_points(meshes: List, MI: np.ndarray, FI: np.ndarray, P_mm: np.ndarray, used: np.ndarray,
                      labels: np.ndarray, n_codes: int, piece_vote_threshold: float = 0.0,
                      vis_mask: np.ndarray | None = None) -> Tuple[List[np.ndarray], List[np.ndarray]]:
    """샘플 점의 라벨을 각 메쉬의 면(face)으로 옮긴다.

    반환: (face_labels, face_visible_fraction) — 면별 라벨과 '그 면의 샘플 중 가시(사용된) 샘플 비율'.
    샘플이 없는 면은 가장 가까운 사용 샘플의 라벨을 받되 가시비율 0 으로 둔다(숨은 뒷면 등).
    """
    tree = cKDTree(P_mm[used])
    if vis_mask is None:  # 가시 마스크가 없으면 사용된 샘플을 가시로 간주
        vis_mask = np.zeros(len(P_mm), dtype=bool)
        vis_mask[used] = True
    used_mask = vis_mask
    out_labels, out_vis = [], []
    for mi, m in enumerate(meshes):
        nf = len(m.faces)
        votes = np.zeros((nf, n_codes), dtype=np.int32)
        sel = np.flatnonzero(MI[used] == mi)
        if len(sel):
            np.add.at(votes, (FI[used][sel], labels[sel]), 1)
        fl = votes.argmax(axis=1)
        none = votes.sum(axis=1) == 0
        if none.any():
            cen = m.triangles_center[none]
            _, j = tree.query(cen, k=1)
            fl[none] = labels[j]
        all_sel = MI == mi
        tot = np.bincount(FI[all_sel], minlength=nf).astype(float)
        vis = np.bincount(FI[all_sel & used_mask], minlength=nf).astype(float)
        frac = np.where(tot > 0, vis / np.maximum(tot, 1), 0.0)
        out_labels.append(fl)
        out_vis.append(frac)
    # 조각(파일) 단위 다수결: 보이는 면적 기준으로 한 파일이 거의 한 부품이면 전부 그 라벨로
    if len(meshes) > 1 and piece_vote_threshold > 0:
        for mi, m in enumerate(meshes):
            wa = m.area_faces * out_vis[mi]
            if wa.sum() <= 0:
                continue
            share = np.bincount(out_labels[mi], weights=wa, minlength=n_codes) / wa.sum()
            if share.max() >= piece_vote_threshold:
                out_labels[mi][:] = share.argmax()
    return out_labels, out_vis


def part_stats(meshes: List, face_labels: List[np.ndarray], face_vis: List[np.ndarray], codes: Sequence[str], frame,
               prob: np.ndarray, used_labels: np.ndarray) -> Dict[str, dict]:
    """부품(코드)별 면적(전체/가시)·bbox(가시 면 기준, 정규화 축 mm 및 원좌표)·중심·신뢰도."""
    stats: Dict[str, dict] = {}
    conf_by = {}
    for ci in np.unique(used_labels):
        conf_by[int(ci)] = float(prob[used_labels == ci].max(axis=1).mean())
    for ci, code in enumerate(codes):
        area_all, area_vis, nfaces = 0.0, 0.0, 0
        Vr, Vo = [], []
        for m, fl, fv in zip(meshes, face_labels, face_vis):
            sel = fl == ci
            if not sel.any():
                continue
            fa = m.area_faces[sel]
            area_all += float(fa.sum())
            area_vis += float((fa * fv[sel]).sum())
            nfaces += int(sel.sum())
            vis_faces = np.flatnonzero(sel & (fv > 0))
            if len(vis_faces) == 0:
                vis_faces = np.flatnonzero(sel)
            v = m.vertices[np.unique(m.faces[vis_faces])]
            Vo.append(v)
            Vr.append(frame.rotate(v))
        if nfaces == 0:
            continue
        Vr, Vo = np.vstack(Vr), np.vstack(Vo)
        ext = Vr.max(0) - Vr.min(0)
        lo, hi = frame.from_rotated(Vr.min(0)[None])[0], frame.from_rotated(Vr.max(0)[None])[0]
        stats[code] = {
            "area_mm2": area_all,
            "area_visible_mm2": area_vis if area_vis > 0 else area_all,
            "n_faces": nfaces,
            "bbox_mm": {"L_u": float(ext[0]), "H_v": float(ext[1]), "D_w": float(ext[2])},
            "bbox_norm": {"umin": float(lo[0]), "umax": float(hi[0]), "vmin": float(lo[1]), "vmax": float(hi[1])},
            "bbox_orig_min": Vo.min(0).tolist(),
            "bbox_orig_max": Vo.max(0).tolist(),
            "centroid_orig": Vo.mean(0).tolist(),
            "confidence": conf_by.get(ci, 0.0),
        }
    return stats


def segment_mesh_files(skin_path: str, library: Library, clf_bundle: Optional[dict], cfg, out_dir: str, log=print) -> dict:
    """스킨 STL(파일 1개 또는 조각 파일 폴더)을 분할하고 결과 파일을 out_dir 에 쓴다."""
    from .visualize import export_colored_mesh, export_part_meshes, label, preview_png

    os.makedirs(out_dir, exist_ok=True)
    files = list_mesh_files(skin_path)
    if not files:
        raise FileNotFoundError(f"메쉬 파일이 없습니다: {skin_path}")
    meshes = []
    for f in files:
        try:
            meshes.append(load_mesh(f))
        except Exception as e:  # noqa: BLE001
            log(f"[경고] 읽기 실패: {f} ({e})")
    log(f"입력 메쉬 {len(meshes)}개, 삼각형 {sum(len(m.faces) for m in meshes):,}개, 총면적 {sum(m.area for m in meshes)/1e6:.3f} m²")
    P, N, MI, FI = sample_meshes(meshes, cfg.skin_samples, seed=cfg.seed)
    res = segment_points(P, N, library, clf_bundle, cfg, log=log)
    used, labels = res["used_idx"], res["labels"]
    face_labels, face_vis = faces_from_points(meshes, MI, FI, P, used, labels, len(library.codes),
                                              piece_vote_threshold=cfg.piece_vote_threshold if len(meshes) > 1 else 0.0,
                                              vis_mask=res["vis_mask"])
    stats = part_stats(meshes, face_labels, face_vis, library.codes, res["frame"], res["prob"], labels)

    # ----- 출력 -----
    preview_png(res["P_norm"], labels, library.codes, os.path.join(out_dir, "preview.png"),
                title=f"{os.path.basename(skin_path)} " + label("분할 결과", "segmentation"))
    export_colored_mesh(meshes, face_labels, library.codes, os.path.join(out_dir, "skin_labeled.ply"))
    part_files = export_part_meshes(meshes, face_labels, library.codes, os.path.join(out_dir, "parts"))
    for code, p in part_files.items():
        stats[code]["stl"] = p
    np.savez_compressed(os.path.join(out_dir, "points_labeled.npz"), points_mm=P[used], points_norm=res["P_norm"],
                        labels=labels, prob=res["prob"].astype(np.float32), codes=np.array(library.codes))
    summary = {
        "input": skin_path, "n_meshes": len(meshes), "codes": library.codes,
        "frame": res["frame"].to_dict(), "mirrored": res["frame"].mirrored, "pose": res["pose_detail"],
        "similar_atlases": [{"atlas_id": a, "chamfer": float(d)} for a, d in res["ranking"][: cfg.k_atlas]],
        "transfer": res["transfer_info"], "parts": stats,
    }
    with open(os.path.join(out_dir, "segmentation.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    with open(os.path.join(out_dir, "parts.csv"), "w", encoding="utf-8-sig") as f:
        f.write("code,area_visible_mm2,area_all_mm2,L_u_mm,H_v_mm,D_w_mm,confidence,n_faces\n")
        for code, s in sorted(stats.items(), key=lambda kv: -kv[1]["area_visible_mm2"]):
            b = s["bbox_mm"]
            f.write(f"{code},{s['area_visible_mm2']:.0f},{s['area_mm2']:.0f},{b['L_u']:.1f},{b['H_v']:.1f},{b['D_w']:.1f},{s['confidence']:.3f},{s['n_faces']}\n")
    log("\n부품별 결과(면적 큰 순):")
    for code, s in sorted(stats.items(), key=lambda kv: -kv[1]["area_visible_mm2"]):
        b = s["bbox_mm"]
        log(f"  {code:20s} 보이는면적 {s['area_visible_mm2']/100:9.1f} cm²  L×H×D = {b['L_u']:.0f}×{b['H_v']:.0f}×{b['D_w']:.0f} mm  신뢰도 {s['confidence']:.2f}")
    summary["_meshes"] = meshes
    summary["_face_labels"] = face_labels
    summary["_result"] = res
    return summary
