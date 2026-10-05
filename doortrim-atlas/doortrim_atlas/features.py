"""점 단위 기하 특징(feature) 계산. 정규화 좌표계(u,v,w)의 점과 법선을 입력으로 받는다.

특징은 '배치(위치)'와 '국소 형상(돌출, 거칠기, 평면성, 트림 가장자리까지 거리)'을 함께 담아
점 분류기가 "이 위치의 이런 형상은 보통 암레스트" 같은 규칙을 배울 수 있게 한다.
"""
from __future__ import annotations

from typing import List, Tuple

import numpy as np
from scipy import ndimage
from scipy.spatial import cKDTree

GRID_LO, GRID_HI = -0.1, 1.1  # 정규화 u,v 격자 범위(여유 포함)


def orient_normals(N: np.ndarray) -> np.ndarray:
    """법선을 실내(+w)쪽으로 향하게 통일한다(STL 서피스는 법선 방향이 임의일 수 있음)."""
    s = np.where(N[:, 2] < 0, -1.0, 1.0)
    return N * s[:, None]


def _grid_index(P: np.ndarray, res: Tuple[int, int]) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    fu = (P[:, 0] - GRID_LO) / (GRID_HI - GRID_LO) * res[0]
    fv = (P[:, 1] - GRID_LO) / (GRID_HI - GRID_LO) * res[1]
    gu = np.clip(np.floor(fu).astype(int), 0, res[0] - 1)
    gv = np.clip(np.floor(fv).astype(int), 0, res[1] - 1)
    return gu, gv, fu, fv


def _fill_nan_nearest(a: np.ndarray) -> np.ndarray:
    mask = np.isnan(a)
    if not mask.any():
        return a
    if mask.all():
        return np.zeros_like(a)
    idx = ndimage.distance_transform_edt(mask, return_distances=False, return_indices=True)
    return a[tuple(idx)]


def base_surface(P: np.ndarray, res: Tuple[int, int] = (32, 16), sigma: float = 1.5) -> np.ndarray:
    """바탕 패널 높이 w_base(u,v)를 격자별 20% 분위수로 추정해 각 점에 보간한다."""
    gu, gv, fu, fv = _grid_index(P, res)
    key = gu * res[1] + gv
    order = np.argsort(key, kind="stable")
    ks, ws = key[order], P[order, 2]
    uniq, start = np.unique(ks, return_index=True)
    base = np.full(res[0] * res[1], np.nan)
    ends = np.append(start[1:], len(ks))
    for u_key, s, e in zip(uniq, start, ends):
        base[u_key] = np.percentile(ws[s:e], 20)
    base = _fill_nan_nearest(base.reshape(res))
    base = ndimage.gaussian_filter(base, sigma, mode="nearest")
    return ndimage.map_coordinates(base, [fu - 0.5, fv - 0.5], order=1, mode="nearest")


def boundary_distance(P: np.ndarray, res: Tuple[int, int] = (120, 60)) -> np.ndarray:
    """트림 외곽(점이 없는 영역)까지의 2D 거리(정규화 단위)."""
    gu, gv, fu, fv = _grid_index(P, res)
    occ = np.zeros(res, dtype=bool)
    occ[gu, gv] = True
    occ = ndimage.binary_closing(occ, structure=np.ones((3, 3)), iterations=2) | occ
    dist = ndimage.distance_transform_edt(occ)
    cell = (GRID_HI - GRID_LO) / res[0]
    return ndimage.map_coordinates(dist, [fu - 0.5, fv - 0.5], order=1, mode="nearest") * cell


def compute_features(P: np.ndarray, N: np.ndarray) -> Tuple[np.ndarray, List[str]]:
    """(n,F) float32 특징 행렬과 특징 이름 목록을 돌려준다."""
    P = np.asarray(P, float)
    N = orient_normals(np.asarray(N, float))
    n = len(P)
    tree = cKDTree(P)
    f = {}
    f["u"], f["v"], f["w"] = P[:, 0], P[:, 1], P[:, 2]
    f["nu"], f["nv"], f["nw"] = N[:, 0], N[:, 1], N[:, 2]
    f["protrusion"] = P[:, 2] - base_surface(P)
    for k, tag in ((12, "s"), (48, "l")):
        kk = min(k + 1, n)
        d, idx = tree.query(P, k=kk)
        idx = idx[:, 1:]
        Nn = N[idx]
        f[f"nvar_{tag}"] = 1.0 - np.linalg.norm(Nn.mean(axis=1), axis=1)
        Wn = P[idx][:, :, 2]
        f[f"wrange_{tag}"] = Wn.max(axis=1) - Wn.min(axis=1)
        f[f"wabove_{tag}"] = P[:, 2] - Wn.min(axis=1)
        if tag == "s":
            X = P[idx] - P[idx].mean(axis=1, keepdims=True)
            C = np.einsum("nki,nkj->nij", X, X) / max(kk - 1, 1)
            ev = np.linalg.eigvalsh(C)  # 오름차순
            f["planarity"] = ev[:, 0] / (ev[:, 2] + 1e-12)
            f["linearity"] = (ev[:, 2] - ev[:, 1]) / (ev[:, 2] + 1e-12)
    f["bdist"] = boundary_distance(P)
    names = list(f.keys())
    F = np.column_stack([f[k] for k in names]).astype(np.float32)
    F[~np.isfinite(F)] = 0.0
    return F, names
