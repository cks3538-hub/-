"""아틀라스 ↔ 신규 스킨 정렬(registration)과 라벨 전이(label transfer).

1) 자세 가설 선택: 8가지 부호 조합 중 기존 아틀라스들과 챔퍼 거리가 가장 작은 것을 고른다.
2) 유사 아틀라스 순위: 챔퍼 거리 기준 상위 k_atlas개.
3) 아틀라스별 정렬: 유사변환 ICP → 아핀 ICP → (선택) 깊이맵 블록매칭 비강체 보정.
4) 라벨 전이: 정렬된 스킨 점마다 아틀라스 이웃 k개의 라벨을 거리 가중 투표 → 확률.
5) 아틀라스 간 융합: 유사도 가중 평균.
"""
from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

import numpy as np
from scipy import ndimage
from scipy.interpolate import RegularGridInterpolator
from scipy.spatial import cKDTree

from .features import GRID_HI, GRID_LO


# ----------------------------------------------------------------------------
# 거리/순위
# ----------------------------------------------------------------------------
def chamfer(a_pts: np.ndarray, a_tree: cKDTree, b_pts: np.ndarray, b_tree: cKDTree | None = None) -> float:
    if b_tree is None:
        b_tree = cKDTree(b_pts)
    d_ab = a_tree.query(b_pts, k=1)[0].mean()
    d_ba = b_tree.query(a_pts, k=1)[0].mean()
    return float(0.5 * (d_ab + d_ba))


def rank_atlases(skin_sub: np.ndarray, library, exclude: Sequence[str] = ()) -> List[Tuple[str, float]]:
    """스킨(정규화 서브샘플)과 모든 아틀라스의 챔퍼 거리를 오름차순으로 돌려준다."""
    skin_tree = cKDTree(skin_sub)
    out = []
    for a in library.atlases:
        if a.atlas_id in exclude:
            continue
        sub, tree = library.subsample_of(a)
        out.append((a.atlas_id, chamfer(sub, tree, skin_sub, skin_tree)))
    out.sort(key=lambda t: t[1])
    return out


def hypothesis_score(ranking: List[Tuple[str, float]], top: int = 3) -> float:
    if not ranking:
        return float("inf")
    return float(np.mean([d for _, d in ranking[:top]]))


# ----------------------------------------------------------------------------
# 정렬(ICP)
# ----------------------------------------------------------------------------
def umeyama(src: np.ndarray, dst: np.ndarray, with_scale: bool = True) -> Tuple[np.ndarray, np.ndarray]:
    """src → dst 최소자승 유사변환(회전+등방 스케일+이동). 반사도 허용한다(좌우 도어)."""
    mu_s, mu_d = src.mean(0), dst.mean(0)
    S, D = src - mu_s, dst - mu_d
    cov = D.T @ S / len(src)
    U, sig, Vt = np.linalg.svd(cov)
    Rm = U @ Vt
    scale = 1.0
    if with_scale:
        var_s = (S ** 2).sum() / len(src)
        scale = float(sig.sum() / max(var_s, 1e-12))
    A = scale * Rm
    t = mu_d - A @ mu_s
    return A, t


def affine_fit(src: np.ndarray, dst: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    X = np.hstack([src, np.ones((len(src), 1))])
    M, *_ = np.linalg.lstsq(X, dst, rcond=None)
    return M[:3].T, M[3]


def icp(src: np.ndarray, dst_pts: np.ndarray, dst_tree: cKDTree, iters: int = 8, trim: float = 0.8,
        affine_from: int = 4) -> Tuple[np.ndarray, np.ndarray]:
    """src 를 dst 에 맞추는 변환 (A, t): x' = A x + t. 앞 반복은 유사변환, 뒤 반복은 아핀."""
    A = np.eye(3)
    t = np.zeros(3)
    for it in range(iters):
        s = src @ A.T + t
        d, j = dst_tree.query(s, k=1)
        keep = d <= np.quantile(d, trim)
        X, Y = src[keep], dst_pts[j[keep]]
        if it < affine_from:
            A_new, t_new = umeyama(X, Y)
        else:
            A_new, t_new = affine_fit(X, Y)
            det = abs(np.linalg.det(A_new))
            if det < 0.6 or det > 1.6:  # 과도한 왜곡 방지
                A_new, t_new = umeyama(X, Y)
        A, t = A_new, t_new
    return A, t


# ----------------------------------------------------------------------------
# 깊이맵 블록매칭 비강체 보정
# ----------------------------------------------------------------------------
def depth_map(P: np.ndarray, res: Tuple[int, int]) -> Tuple[np.ndarray, np.ndarray]:
    fu = (P[:, 0] - GRID_LO) / (GRID_HI - GRID_LO) * res[0]
    fv = (P[:, 1] - GRID_LO) / (GRID_HI - GRID_LO) * res[1]
    gu = np.clip(np.floor(fu).astype(int), 0, res[0] - 1)
    gv = np.clip(np.floor(fv).astype(int), 0, res[1] - 1)
    D = np.full(res, -np.inf)
    np.maximum.at(D, (gu, gv), P[:, 2])
    O = np.isfinite(D)
    D[~O] = 0.0
    return D, O


def estimate_uv_warp(src: np.ndarray, atlas_pts: np.ndarray, res: Tuple[int, int] = (96, 48),
                     block: int = 12, search: int = 6, step: int = 8, lam: float = 1.0,
                     sigma: float = 1.0) -> Tuple[np.ndarray, np.ndarray]:
    """src 점의 (u,v)를 아틀라스 배치에 맞추는 변위장(du, dv)을 깊이맵 블록매칭으로 추정한다.

    * 깊이맵: (u,v) 셀마다 최대 w (실내쪽 표면)
    * 각 제어점 주변 블록에서 SSD(깊이) + 점유도 차이를 최소화하는 정수 이동량을 찾고,
      가우시안 평활 후 각 점에 보간한다.
    """
    Ds, Os = depth_map(src, res)
    Da, Oa = depth_map(atlas_pts, res)
    scale = np.std(np.concatenate([Ds[Os], Da[Oa]])) + 1e-9
    Ds, Da = Ds / scale, Da / scale
    pad = search + block
    Dsp = np.pad(Ds, pad)
    Osp = np.pad(Os, pad).astype(float)
    Dap = np.pad(Da, pad)
    Oap = np.pad(Oa, pad).astype(float)
    b2 = block // 2
    cu = np.arange(b2, res[0], step)
    cv = np.arange(b2, res[1], step)
    DU = np.zeros((len(cu), len(cv)))
    DV = np.zeros((len(cu), len(cv)))
    shifts = [(dx, dy) for dx in range(-search, search + 1) for dy in range(-search, search + 1)]
    for i, x in enumerate(cu):
        for j, y in enumerate(cv):
            x0, y0 = x + pad - b2, y + pad - b2
            ws_d = Dsp[x0:x0 + block, y0:y0 + block]
            ws_o = Osp[x0:x0 + block, y0:y0 + block]
            if ws_o.sum() < 0.3 * block * block:
                continue
            best, best_s = np.inf, (0, 0)
            for dx, dy in shifts:
                wa_d = Dap[x0 + dx:x0 + dx + block, y0 + dy:y0 + dy + block]
                wa_o = Oap[x0 + dx:x0 + dx + block, y0 + dy:y0 + dy + block]
                m = ws_o * wa_o
                n_ov = m.sum()
                if n_ov < 0.3 * block * block:
                    continue
                cost = (m * (ws_d - wa_d) ** 2).sum() / n_ov + lam * np.abs(ws_o - wa_o).mean()
                cost += 0.002 * (dx * dx + dy * dy)  # 작은 이동 선호
                if cost < best:
                    best, best_s = cost, (dx, dy)
            DU[i, j], DV[i, j] = best_s
    DU = ndimage.gaussian_filter(DU, sigma, mode="nearest")
    DV = ndimage.gaussian_filter(DV, sigma, mode="nearest")
    cell_u = (GRID_HI - GRID_LO) / res[0]
    cell_v = (GRID_HI - GRID_LO) / res[1]
    # 제어점 중심의 정규화 좌표
    cu_n = GRID_LO + (cu + 0.5) * cell_u
    cv_n = GRID_LO + (cv + 0.5) * cell_v
    fu = RegularGridInterpolator((cu_n, cv_n), DU * cell_u, bounds_error=False, fill_value=None)
    fv = RegularGridInterpolator((cu_n, cv_n), DV * cell_v, bounds_error=False, fill_value=None)
    uv = np.column_stack([np.clip(src[:, 0], cu_n[0], cu_n[-1]), np.clip(src[:, 1], cv_n[0], cv_n[-1])])
    return fu(uv), fv(uv)


# ----------------------------------------------------------------------------
# 부품 단위 국소 정렬(part-wise local registration)
# ----------------------------------------------------------------------------
def highpass_depth(D: np.ndarray, O: np.ndarray, sigma: float = 4.0) -> np.ndarray:
    """깊이맵에서 완만한 바탕 곡면을 빼고(고역통과) 작은 돌출/함몰만 남긴다."""
    Of = O.astype(float)
    num = ndimage.gaussian_filter(np.where(O, D, 0.0), sigma)
    den = ndimage.gaussian_filter(Of, sigma)
    base = num / np.maximum(den, 1e-6)
    hp = D - base
    hp[~O] = 0.0
    return hp


def partwise_shifts(Q: np.ndarray, atlas_pts: np.ndarray, atlas_labels: np.ndarray, res: Tuple[int, int] = (120, 60),
                    search: int = 6, margin: int = 2, min_ncc: float = 0.3, max_frac: float = 0.25) -> Dict[int, Tuple[float, float, float]]:
    """아틀라스의 부품(코드)마다, 스킨 깊이맵과 가장 잘 맞는 (du, dv) 이동량을 NCC 블록매칭으로 찾는다.

    Q: 아틀라스 좌표계로 정렬된 스킨 점. 반환: {코드: (du, dv, ncc)} (정규화 단위).
    큰 부품(점유 셀의 max_frac 이상)은 이동시키지 않는다(경계가 깊이 신호로 안 보이므로 평균 배치를 그대로 쓴다).
    """
    Ds, Os = depth_map(Q, res)
    Da, Oa = depth_map(atlas_pts, res)
    Hs, Ha = highpass_depth(Ds, Os), highpass_depth(Da, Oa)
    cell_u = (GRID_HI - GRID_LO) / res[0]
    cell_v = (GRID_HI - GRID_LO) / res[1]
    gu = np.clip(np.floor((atlas_pts[:, 0] - GRID_LO) / cell_u).astype(int), 0, res[0] - 1)
    gv = np.clip(np.floor((atlas_pts[:, 1] - GRID_LO) / cell_v).astype(int), 0, res[1] - 1)
    n_occ = max(int(Oa.sum()), 1)
    out: Dict[int, Tuple[float, float, float]] = {}
    for c in np.unique(atlas_labels):
        sel = atlas_labels == c
        M = np.zeros(res, dtype=bool)
        M[gu[sel], gv[sel]] = True
        if M.sum() > max_frac * n_occ or M.sum() < 4:
            continue
        M = ndimage.binary_dilation(M, iterations=margin)
        cells = np.argwhere(M)
        a_vals = Ha[cells[:, 0], cells[:, 1]]
        if a_vals.std() < 1e-9:
            continue
        best, best_ncc = (0, 0), -np.inf
        for dx in range(-search, search + 1):
            for dy in range(-search, search + 1):
                cu, cv = cells[:, 0] + dx, cells[:, 1] + dy
                valid = (cu >= 0) & (cu < res[0]) & (cv >= 0) & (cv < res[1])
                if valid.mean() < 0.75:
                    continue
                s_occ = Os[cu[valid], cv[valid]]
                if s_occ.mean() < 0.6:
                    continue
                both = np.flatnonzero(valid)[s_occ]        # 양쪽 다 점이 있는 셀만 비교
                if len(both) < 4:
                    continue
                av, sv = a_vals[both], Hs[cu[both], cv[both]]
                sd = av.std() * sv.std()
                if sd < 1e-12:
                    continue
                ncc = float(((av - av.mean()) * (sv - sv.mean())).mean() / sd) - 0.01 * np.hypot(dx, dy)
                if ncc > best_ncc:
                    best_ncc, best = ncc, (dx, dy)
        if best_ncc >= min_ncc and best != (0, 0):
            out[int(c)] = (best[0] * cell_u, best[1] * cell_v, best_ncc)
    return out


# ----------------------------------------------------------------------------
# 라벨 전이
# ----------------------------------------------------------------------------
def transfer_labels(skin_P: np.ndarray, library, ranking: List[Tuple[str, float]], n_codes: int, cfg,
                    log=print) -> Tuple[np.ndarray, List[Dict]]:
    """상위 k_atlas개 아틀라스에서 라벨 확률(n, n_codes)을 전이한다."""
    n = len(skin_P)
    chosen = ranking[: cfg.k_atlas]
    if not chosen:
        raise ValueError("라이브러리에 아틀라스가 없습니다.")
    d_best = chosen[0][1]
    acc = np.zeros((n, n_codes))
    wsum = np.zeros((n, 1))
    info = []
    rows = np.repeat(np.arange(n), cfg.k_nn)
    for aid, dist in chosen:
        a = library.get(aid)
        tree = a.tree
        A, t = icp(skin_P, a.points, tree, iters=cfg.icp_iters)
        Q = skin_P @ A.T + t
        n_shift = 0
        if cfg.nonrigid == "grid":
            du, dv = estimate_uv_warp(Q, a.points, block=cfg.nonrigid_block, search=cfg.nonrigid_search)
            Q = Q.copy()
            Q[:, 0] += du
            Q[:, 1] += dv
        elif cfg.nonrigid == "parts":
            shifts = partwise_shifts(Q, a.points, a.labels, search=cfg.nonrigid_search, min_ncc=cfg.partwise_min_ncc,
                                     max_frac=cfg.partwise_max_frac)
            if shifts:
                pts = a.points.copy()
                for c, (du, dv, _ncc) in shifts.items():
                    sel = a.labels == c
                    pts[sel, 0] += du
                    pts[sel, 1] += dv
                tree = cKDTree(pts)
                n_shift = len(shifts)
        d, j = tree.query(Q, k=cfg.k_nn)
        wgt = np.exp(-(d / cfg.nn_sigma) ** 2) + 1e-9
        lab = a.labels[j]
        p = np.zeros((n, n_codes))
        np.add.at(p, (rows, lab.ravel()), wgt.ravel())
        p /= p.sum(axis=1, keepdims=True)
        conf = np.exp(-(d[:, 0] / (3 * cfg.nn_sigma)) ** 2)[:, None]  # 가까운 대응이 없으면 신뢰 낮음
        w_a = float(np.exp(-(dist - d_best) / max(d_best, 1e-6)))
        acc += w_a * conf * p
        wsum += w_a * conf
        info.append({"atlas_id": aid, "chamfer": float(dist), "weight": w_a, "n_part_shifts": n_shift,
                     "icp_det": float(np.linalg.det(A)), "mean_nn_dist": float(d[:, 0].mean())})
        log(f"    · {aid}: chamfer={dist:.4f} weight={w_a:.2f} 평균대응거리={d[:, 0].mean():.4f} 부품이동 {n_shift}건")
    prob = acc / np.maximum(wsum, 1e-9)
    empty = wsum[:, 0] < 1e-6
    if empty.any():
        prob[empty] = 1.0 / n_codes
    return prob, info
