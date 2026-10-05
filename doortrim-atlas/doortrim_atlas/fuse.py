"""아틀라스 확률 + 분류기 확률 융합, 그래프 평활, 작은 조각 정리."""
from __future__ import annotations

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree


def fuse_probs(prob_atlas: np.ndarray, prob_clf: np.ndarray | None, alpha: float, mode: str = "loglinear",
               eps: float = 0.02) -> np.ndarray:
    """아틀라스 확률과 분류기 확률을 융합한다.

    loglinear(기본): p ∝ (pa+eps)^alpha · (pc+eps)^(1-alpha). 한쪽이 확신하면 다른 쪽의 '희석된' 투표를
    이길 수 있어 작은 부품에 유리하다.  linear: 단순 가중 평균.
    """
    if prob_clf is None:
        return prob_atlas
    if mode == "linear":
        return alpha * prob_atlas + (1.0 - alpha) * prob_clf
    lp = alpha * np.log(prob_atlas + eps) + (1.0 - alpha) * np.log(prob_clf + eps)
    lp -= lp.max(axis=1, keepdims=True)
    p = np.exp(lp)
    return p / p.sum(axis=1, keepdims=True)


def knn_graph(P: np.ndarray, k: int):
    tree = cKDTree(P)
    kk = min(k + 1, len(P))
    d, idx = tree.query(P, k=kk)
    return d[:, 1:], idx[:, 1:]


def smooth_probs(P: np.ndarray, N: np.ndarray, prob: np.ndarray, k: int = 10, iters: int = 10, lam: float = 0.7) -> np.ndarray:
    """이웃 평균으로 확률을 평활한다(법선이 비슷한 이웃에 더 큰 가중). 원래 확률에 고정(anchor)해 과도 평활을 막는다."""
    _, idx = knn_graph(P, k)
    nsim = np.clip((N[:, None, :] * N[idx]).sum(-1), 0.0, 1.0)
    w = 0.3 + 0.7 * nsim
    w /= w.sum(axis=1, keepdims=True)
    P0 = prob.copy()
    cur = prob.copy()
    for _ in range(iters):
        cur = (1.0 - lam) * P0 + lam * np.einsum("nk,nkc->nc", w, cur[idx])
    return cur


def clean_components(P: np.ndarray, labels: np.ndarray, k: int = 10, min_frac: float = 0.001, max_passes: int = 3,
                     expected_frac: np.ndarray | None = None, size_coef: float = 0.2) -> np.ndarray:
    """같은 라벨로 연결된 작은 조각을 주변 다수 라벨로 흡수한다.

    '작다'의 기준은 코드별로 다르다: expected_frac[c] (아틀라스에서 그 부품이 차지하는 평균 점 비율)의
    size_coef 배보다 작으면 조각으로 본다. 그래서 세일 가니시처럼 원래 작은 부품은 살아남고,
    메인 트림이 잘못 튄 작은 섬은 흡수된다. 절대 하한은 min_frac·n.
    """
    n = len(P)
    labels = labels.copy()
    _, idx = knn_graph(P, k)
    rows = np.repeat(np.arange(n), idx.shape[1])
    cols = idx.ravel()
    floor = max(int(min_frac * n), 5)
    for _ in range(max_passes):
        same = labels[rows] == labels[cols]
        G = coo_matrix((np.ones(same.sum()), (rows[same], cols[same])), shape=(n, n))
        ncomp, comp = connected_components(G, directed=False)
        sizes = np.bincount(comp, minlength=ncomp)
        comp_label = np.zeros(ncomp, dtype=int)
        comp_label[comp] = labels
        if expected_frac is not None:
            thr = np.maximum(floor, np.minimum(size_coef * expected_frac[comp_label] * n, 0.05 * n))
        else:
            thr = np.full(ncomp, floor, dtype=float)
        small = np.flatnonzero(sizes < thr)
        if len(small) == 0:
            break
        changed = False
        for c in small:
            members = np.flatnonzero(comp == c)
            nb = idx[members].ravel()
            outside = nb[comp[nb] != c]
            if len(outside) == 0:
                continue
            new = np.bincount(labels[outside]).argmax()
            if new != labels[members[0]]:
                labels[members] = new
                changed = True
        if not changed:
            break
    return labels
