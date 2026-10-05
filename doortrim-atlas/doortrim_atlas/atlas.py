"""아틀라스(라벨이 붙은 과거 차종 1건)와 아틀라스 라이브러리.

아틀라스 1건 = 과거 차종 도어트림 1개의 모든 Body(STL)를 조립 상태로 합친 뒤,
실내에서 보이는 A면 점들만 남기고 정규화 좌표계로 옮긴 '라벨 있는 점구름'.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
from scipy.spatial import cKDTree

from .features import compute_features
from .geometry import load_mesh, sample_meshes, solidness, stratified_subsample, random_subsample, view_directions, visible_mask
from .pose import Frame, heuristic_rotation, pca_axes, sign_hypotheses
from . import transfer


@dataclass
class Atlas:
    atlas_id: str
    points: np.ndarray          # (n,3) 정규화 좌표
    normals: np.ndarray         # (n,3)
    labels: np.ndarray          # (n,) int, library.codes 인덱스
    features: np.ndarray        # (n,F)
    frame: Frame
    parts: Dict[str, dict]      # code -> 통계(면적, bbox, 중량 ...)
    meta: dict = field(default_factory=dict)
    _tree: Optional[cKDTree] = None

    @property
    def tree(self) -> cKDTree:
        if self._tree is None:
            self._tree = cKDTree(self.points)
        return self._tree


class Library:
    """아틀라스 모음. 디스크 구조:
        <path>/library.json              코드 목록, 아틀라스 ID 목록, 특징 이름
        <path>/atlases/<id>.npz          points, normals, labels, features
        <path>/atlases/<id>.json         frame, parts, meta
    """

    def __init__(self, path: Optional[str] = None):
        self.path = path
        self.codes: List[str] = []
        self.feature_names: List[str] = []
        self.atlases: List[Atlas] = []
        self._by_id: Dict[str, Atlas] = {}
        self._subs: Dict[str, Tuple[np.ndarray, cKDTree]] = {}
        self.meta: dict = {}

    # ---------- 코드 ----------
    def code_index(self, code: str) -> int:
        if code not in self.codes:
            self.codes.append(code)
        return self.codes.index(code)

    # ---------- 조회 ----------
    def get(self, atlas_id: str) -> Atlas:
        return self._by_id[atlas_id]

    def __len__(self) -> int:
        return len(self.atlases)

    def add(self, atlas: Atlas) -> None:
        if atlas.atlas_id in self._by_id:
            self.atlases = [a for a in self.atlases if a.atlas_id != atlas.atlas_id]
        self.atlases.append(atlas)
        self._by_id[atlas.atlas_id] = atlas
        self._subs.pop(atlas.atlas_id, None)

    def expected_label_fractions(self, exclude: Sequence[str] = ()) -> np.ndarray:
        """코드별 '아틀라스에서 차지하는 평균 점 비율'(없는 코드는 작은 값)."""
        n_codes = len(self.codes)
        acc = np.zeros(n_codes)
        cnt = np.zeros(n_codes)
        for a in self.atlases:
            if a.atlas_id in exclude:
                continue
            frac = np.bincount(a.labels, minlength=n_codes) / max(len(a.labels), 1)
            present = frac > 0
            acc[present] += frac[present]
            cnt[present] += 1
        out = np.where(cnt > 0, acc / np.maximum(cnt, 1), 0.002)
        return out

    def subsample_of(self, atlas: Atlas, n: int = 3000) -> Tuple[np.ndarray, cKDTree]:
        if atlas.atlas_id not in self._subs:
            rng = np.random.default_rng(0)
            idx = random_subsample(len(atlas.points), n, rng)
            sub = atlas.points[idx]
            self._subs[atlas.atlas_id] = (sub, cKDTree(sub))
        return self._subs[atlas.atlas_id]

    def without(self, atlas_id: str) -> "Library":
        """아틀라스 하나를 제외한 뷰(평가용). 데이터는 공유한다."""
        lib = Library(self.path)
        lib.codes = self.codes
        lib.feature_names = self.feature_names
        lib.meta = self.meta
        for a in self.atlases:
            if a.atlas_id != atlas_id:
                lib.add(a)
        lib._subs = {k: v for k, v in self._subs.items() if k != atlas_id}
        return lib

    # ---------- 저장/불러오기 ----------
    def save(self, path: Optional[str] = None) -> None:
        path = path or self.path
        if not path:
            raise ValueError("저장 경로가 없습니다.")
        self.path = path
        os.makedirs(os.path.join(path, "atlases"), exist_ok=True)
        for a in self.atlases:
            np.savez_compressed(os.path.join(path, "atlases", f"{a.atlas_id}.npz"),
                                points=a.points.astype(np.float32), normals=a.normals.astype(np.float32),
                                labels=a.labels.astype(np.int32), features=a.features.astype(np.float32))
            with open(os.path.join(path, "atlases", f"{a.atlas_id}.json"), "w", encoding="utf-8") as f:
                json.dump({"frame": a.frame.to_dict(), "parts": a.parts, "meta": a.meta}, f, ensure_ascii=False, indent=1)
        with open(os.path.join(path, "library.json"), "w", encoding="utf-8") as f:
            json.dump({"codes": self.codes, "feature_names": self.feature_names,
                       "atlas_ids": [a.atlas_id for a in self.atlases], "meta": self.meta}, f, ensure_ascii=False, indent=1)

    @classmethod
    def load(cls, path: str) -> "Library":
        lib = cls(path)
        with open(os.path.join(path, "library.json"), "r", encoding="utf-8") as f:
            info = json.load(f)
        lib.codes = list(info["codes"])
        lib.feature_names = list(info.get("feature_names", []))
        lib.meta = info.get("meta", {})
        for aid in info["atlas_ids"]:
            z = np.load(os.path.join(path, "atlases", f"{aid}.npz"))
            with open(os.path.join(path, "atlases", f"{aid}.json"), "r", encoding="utf-8") as f:
                j = json.load(f)
            lib.add(Atlas(aid, z["points"].astype(np.float64), z["normals"].astype(np.float64),
                          z["labels"].astype(np.int64), z["features"].astype(np.float32),
                          Frame.from_dict(j["frame"]), j["parts"], j.get("meta", {})))
        return lib


# ----------------------------------------------------------------------------
# 자세 선택(공통): 원시 점 → 회전 가설 → 가시성 → 정규화 → 라이브러리와 비교
# ----------------------------------------------------------------------------
def choose_frame(P: np.ndarray, N: np.ndarray, library: Library, cfg, log=print,
                 is_reference: bool = False, exclude: Sequence[str] = ()) -> Tuple[Frame, np.ndarray, np.ndarray, dict]:
    """P(mm), 법선 N 전체에 대한 (Frame, 가시 마스크, 부호 정리된 법선, 상세정보)를 돌려준다.

    라이브러리가 비어 있으면(기준 아틀라스) 차량좌표계 관례와 거칠기 검사로 방향을 정하고,
    그렇지 않으면 8가지 부호 가설 중 기존 아틀라스들과 가장 잘 맞는 것을 고른다.
    """
    rng = np.random.default_rng(cfg.seed)
    c, axes, _ = pca_axes(P)
    R0, how = heuristic_rotation(P, axes, cabin_dir=cfg.cabin_dir, up_dir=cfg.up_dir, rear_dir=cfg.rear_dir)
    dirs = view_directions(cfg.vis_tilt_deg)
    hyps = sign_hypotheses(R0)
    sub_idx = random_subsample(len(P), cfg.hyp_samples, rng)
    Ps, Ns = P[sub_idx], N[sub_idx]
    detail = {"heuristic": how}

    # 솔리드(양면) 여부와 법선 부호 정리 — 방향 가설과 무관하게 판정됨
    Q0 = (Ps - c) @ R0.T
    solid_like, Ns_fixed, both = solidness(Q0, Ns @ R0.T, cell=cfg.vis_cell_mm, tol=cfg.vis_tol_mm)
    flipped = bool(np.any(Ns_fixed != Ns @ R0.T))
    N = -N if flipped else N
    cull = solid_like and cfg.backface_cull
    detail.update({"solid_like": solid_like, "two_sided_fraction": both, "normals_flipped": flipped, "backface_cull": cull})
    log(f"  입력 성격: {'솔리드(앞뒷면 있음)' if solid_like else '서피스(한 겹)'} (양면겹침 {both:.2f}), 뒷면제거={cull}")
    Ns = N[sub_idx]

    def _vis(Q, Nr):
        return visible_mask(Q, dirs, cell=cfg.vis_cell_mm, tol=cfg.vis_tol_mm, normals=Nr, cull=cull)

    usable = [a for a in library.atlases if a.atlas_id not in exclude]
    if usable and not is_reference:
        scores = []
        for h, R in enumerate(hyps):
            Q = (Ps - c) @ R.T
            vis = _vis(Q, Ns @ R.T)
            if vis.sum() < 100:
                scores.append(float("inf"))
                continue
            fr = Frame.fit(c, R, Q[vis])
            X = fr.from_rotated(Q[vis])
            X = X[random_subsample(len(X), 3000, rng)]
            ranking = transfer.rank_atlases(X, library, exclude=exclude)
            scores.append(transfer.hypothesis_score(ranking))
        best = int(np.argmin(scores))
        R = hyps[best]
        detail.update({"hypothesis": best, "scores": [float(s) for s in scores]})
        log(f"  자세 가설 선택: #{best} (점수 {scores[best]:.4f}, 2위 {sorted(scores)[1]:.4f}) [휴리스틱: {how}]")
    else:
        R = R0
        # 기준 아틀라스: 솔리드라면 두 w 부호 중 '덜 거친' 면(A면)을 고른다(뒷면은 리브 때문에 거칠다).
        if solid_like and cfg.cabin_dir == "auto":
            Q = (Ps - c) @ R.T
            vis_pos = _vis(Q, Ns @ R.T)
            Rn = R.copy()
            Rn[2] *= -1
            Qn = (Ps - c) @ Rn.T
            vis_neg = _vis(Qn, Ns @ Rn.T)
            r_pos = _roughness(Q[vis_pos], Ns[vis_pos] @ R.T)
            r_neg = _roughness(Qn[vis_neg], Ns[vis_neg] @ Rn.T)
            detail.update({"roughness_pos": r_pos, "roughness_neg": r_neg})
            if r_neg < r_pos * 0.9 and how.startswith("skewness"):
                R = Rn
                log(f"  기준 아틀라스 w 부호 반전(거칠기 {r_pos:.3f} → {r_neg:.3f})")
        log(f"  기준 아틀라스 자세: {how}")
    Q_all = (P - c) @ R.T
    vis_all = _vis(Q_all, N @ R.T)
    frame = Frame.fit(c, R, Q_all[vis_all])
    return frame, vis_all, N, detail


def _roughness(Q: np.ndarray, N: np.ndarray, k: int = 12) -> float:
    if len(Q) < k + 1:
        return 1.0
    idx = cKDTree(Q).query(Q, k=k + 1)[1][:, 1:]
    N = N * np.where(N[:, 2:3] < 0, -1.0, 1.0)
    return float((1.0 - np.linalg.norm(N[idx].mean(axis=1), axis=1)).mean())


# ----------------------------------------------------------------------------
# 아틀라스 구축
# ----------------------------------------------------------------------------
def build_atlas(atlas_id: str, bodies: List[dict], library: Library, cfg, log=print) -> Optional[Atlas]:
    """bodies: [{'path':..., 'code':..., 'weight_g':..., 'material':..., ...}, ...]"""
    meshes, kept = [], []
    for b in bodies:
        try:
            meshes.append(load_mesh(b["path"]))
            kept.append(b)
        except Exception as e:  # noqa: BLE001
            log(f"  [경고] 읽기 실패, 건너뜀: {b['path']} ({e})")
    if not meshes:
        log(f"  [오류] {atlas_id}: 읽을 수 있는 STL이 없습니다.")
        return None
    bodies = kept
    rng = np.random.default_rng(cfg.seed)
    P, N, MI, FI = sample_meshes(meshes, cfg.raw_samples, seed=cfg.seed)
    frame, vis, N, detail = choose_frame(P, N, library, cfg, log=log, is_reference=(len(library) == 0))
    vis_idx = np.flatnonzero(vis)
    keep = vis_idx[stratified_subsample(MI[vis_idx], cfg.atlas_points, rng)]
    Pn = frame.normalize(P[keep])
    Nn = frame.rotate_vec(N[keep])
    codes = [b["code"] for b in bodies]
    labels = np.array([library.code_index(codes[i]) for i in MI[keep]], dtype=np.int64)
    F, names = compute_features(Pn, Nn)
    if not library.feature_names:
        library.feature_names = names

    # 부품(코드)별 통계
    parts: Dict[str, dict] = {}
    for i, (m, b) in enumerate(zip(meshes, bodies)):
        sel = MI == i
        vfrac = float(vis[sel].mean()) if sel.any() else 0.0
        V = frame.rotate(m.vertices)
        ext_mm = (V.max(0) - V.min(0)).tolist()
        Vn = frame.from_rotated(V)
        cen = Pn[MI[keep] == i].mean(axis=0).tolist() if (MI[keep] == i).any() else Vn.mean(axis=0).tolist()
        st = parts.setdefault(b["code"], {"area_total_mm2": 0.0, "area_visible_mm2": 0.0, "n_bodies": 0,
                                           "weight_g": 0.0, "weight_known": True, "materials": [],
                                           "bbox_mm": [0.0, 0.0, 0.0], "bbox_min_n": [np.inf] * 3,
                                           "bbox_max_n": [-np.inf] * 3, "centroid_n": None,
                                           "n_visible_pts": 0, "visible_fraction": 0.0, "body_names": []})
        st["area_total_mm2"] += float(m.area)
        st["area_visible_mm2"] += float(m.area) * vfrac
        st["n_bodies"] += 1
        w = b.get("weight_g")
        if w is None or (isinstance(w, float) and np.isnan(w)) or w == "":
            st["weight_known"] = False
        else:
            st["weight_g"] += float(w)
        if b.get("material"):
            st["materials"].append(str(b["material"]))
        st["bbox_mm"] = [max(x, y) for x, y in zip(st["bbox_mm"], ext_mm)]
        vis_body = frame.rotate(P[(MI == i) & vis]) if ((MI == i) & vis).any() else None
        if vis_body is not None and len(vis_body) >= 3:
            ext_vis = (vis_body.max(0) - vis_body.min(0)).tolist()
            st["bbox_visible_mm"] = [max(x, y) for x, y in zip(st.get("bbox_visible_mm", [0.0, 0.0, 0.0]), ext_vis)]
        st["bbox_min_n"] = [min(x, y) for x, y in zip(st["bbox_min_n"], Vn.min(0).tolist())]
        st["bbox_max_n"] = [max(x, y) for x, y in zip(st["bbox_max_n"], Vn.max(0).tolist())]
        st["n_visible_pts"] += int((MI[keep] == i).sum())
        st["body_names"].append(str(b.get("body_name", os.path.basename(b["path"]))))
        st["_cen_acc"] = st.get("_cen_acc", []) + [cen]
    for code, st in parts.items():
        st.setdefault("bbox_visible_mm", st["bbox_mm"])
        st["visible_fraction"] = st["area_visible_mm2"] / max(st["area_total_mm2"], 1e-9)
        st["centroid_n"] = np.mean(st.pop("_cen_acc"), axis=0).tolist()
        if not st["weight_known"]:
            st["weight_g"] = None
        st["material"] = max(set(st["materials"]), key=st["materials"].count) if st["materials"] else None
        st.pop("materials")
        st["bbox_n"] = [b - a for a, b in zip(st["bbox_min_n"], st["bbox_max_n"])]
    meta = {"n_bodies": len(bodies), "n_raw_samples": int(len(P)), "visible_fraction_all": float(vis.mean()),
            "mirrored": frame.mirrored, "pose": detail, "source_paths": [b["path"] for b in bodies]}
    for k in ("vehicle", "side", "part_no", "note"):
        vals = {str(b[k]) for b in bodies if b.get(k) not in (None, "")}
        if vals:
            meta[k] = sorted(vals)[0] if len(vals) == 1 else sorted(vals)
    atlas = Atlas(atlas_id, Pn, Nn, labels, F, frame, parts, meta)
    library.add(atlas)
    log(f"  완료: {atlas_id} 바디 {len(bodies)}개, 가시점 {len(Pn):,}개, 가시비율 {vis.mean():.2f}, 거울상={frame.mirrored}")
    return atlas


def read_manifest(path: str) -> Dict[str, List[dict]]:
    """manifest.csv → {atlas_id: [body dict, ...]}"""
    import csv
    groups: Dict[str, List[dict]] = {}
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        required = {"atlas_id", "stl_path", "standard_part_code"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"manifest 에 필요한 열이 없습니다: {sorted(missing)} (있는 열: {reader.fieldnames})")
        for row in reader:
            code = (row.get("standard_part_code") or "").strip()
            if not code:
                continue
            b = {"path": row["stl_path"].strip(), "code": code}
            w = (row.get("weight_g") or "").strip()
            b["weight_g"] = float(w) if w not in ("", "None", "nan") else None
            for k in ("material", "vehicle", "side", "body_name", "part_no", "note"):
                if row.get(k):
                    b[k] = row[k].strip()
            groups.setdefault(row["atlas_id"].strip(), []).append(b)
    return groups
