"""메쉬 입출력, 표면 점 샘플링, 가시성(z-buffer) 필터.

CATIA가 STL을 내보낼 때 면(face) 단위로 쪼개 저장해 같은 부품 안에도 틈이 생기는데,
이 모듈은 메쉬 연결성(connectivity)에 전혀 의존하지 않고 '표면 위의 점'만 다루므로
그 문제를 피해 간다.
"""
from __future__ import annotations

import glob
import os
from typing import Iterable, List, Sequence, Tuple

import numpy as np
import trimesh

MESH_EXTS = (".stl", ".obj", ".ply", ".off", ".glb", ".gltf")


def load_mesh(path: str) -> trimesh.Trimesh:
    """STL/OBJ/PLY 파일 하나를 단일 Trimesh로 읽는다."""
    m = trimesh.load(path, force="mesh", process=False)
    if isinstance(m, trimesh.Scene):
        geoms = [g for g in m.geometry.values() if isinstance(g, trimesh.Trimesh)]
        if not geoms:
            raise ValueError(f"메쉬가 비어 있습니다: {path}")
        m = trimesh.util.concatenate(geoms)
    if not isinstance(m, trimesh.Trimesh) or len(m.faces) == 0:
        raise ValueError(f"메쉬를 읽을 수 없습니다: {path}")
    return m


def list_mesh_files(path: str) -> List[str]:
    """파일이면 그 파일 하나, 폴더면 폴더 안의 메쉬 파일 전체(정렬)를 돌려준다."""
    if os.path.isdir(path):
        files = [p for p in glob.glob(os.path.join(path, "*")) if p.lower().endswith(MESH_EXTS)]
        return sorted(files)
    return [path]


def sample_meshes(
    meshes: Sequence[trimesh.Trimesh], n_total: int, seed: int = 0, min_per_mesh: int = 50
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """여러 메쉬에서 면적에 비례해 총 n_total개 점을 샘플링한다.

    Returns
    -------
    points (N,3) mm, normals (N,3), mesh_idx (N,), face_idx (N,)
    """
    areas = np.array([float(m.area) for m in meshes])
    total = areas.sum()
    if total <= 0:
        raise ValueError("면적이 0인 메쉬만 있습니다.")
    counts = np.round(n_total * areas / total).astype(int)
    counts = np.where(areas > 0, np.maximum(counts, min_per_mesh), 0)
    P, N, MI, FI = [], [], [], []
    for i, (m, c) in enumerate(zip(meshes, counts)):
        if c <= 0:
            continue
        pts, fidx = trimesh.sample.sample_surface(m, int(c), seed=seed + i)
        P.append(np.asarray(pts, dtype=np.float64))
        N.append(np.asarray(m.face_normals[fidx], dtype=np.float64))
        MI.append(np.full(len(pts), i, dtype=np.int64))
        FI.append(np.asarray(fidx, dtype=np.int64))
    return np.vstack(P), np.vstack(N), np.concatenate(MI), np.concatenate(FI)


def view_directions(tilt_deg: float = 30.0) -> np.ndarray:
    """정면(+w) 1개와 상하좌우로 기울인 4개 시선 방향(정규화 좌표계 기준)."""
    t = np.deg2rad(tilt_deg)
    s, c = np.sin(t), np.cos(t)
    return np.array([[0, 0, 1], [s, 0, c], [-s, 0, c], [0, s, c], [0, -s, c]], dtype=np.float64)


def _project_basis(d: np.ndarray):
    d = d / np.linalg.norm(d)
    a = np.cross(d, [0.0, 0.0, 1.0])
    if np.linalg.norm(a) < 1e-6:
        a = np.cross(d, [1.0, 0.0, 0.0])
    a /= np.linalg.norm(a)
    b = np.cross(d, a)
    return a, b, d


def depth_visible(points: np.ndarray, normals: np.ndarray | None, d: np.ndarray, cell: float = 4.0,
                  tol: float = 1.5, span: int = 3, max_tol: float = 10.0, cull: bool = False) -> np.ndarray:
    """시선 방향 d 에서 보이는 점(z-buffer). 법선이 있으면 기울기에 맞춰 허용오차를 키우고,
    cull=True 면 뒷면(n·d<0)도 제거한다."""
    from scipy import ndimage
    a, b, d = _project_basis(np.asarray(d, float))
    x, y, z = points @ a, points @ b, points @ d
    ix = np.floor((x - x.min()) / cell).astype(np.int64)
    iy = np.floor((y - y.min()) / cell).astype(np.int64)
    Z = np.full((ix.max() + 1, iy.max() + 1), -np.inf)
    np.maximum.at(Z, (ix, iy), z)
    Zn = ndimage.maximum_filter(Z, size=span, mode="constant", cval=-np.inf)
    zmax = Zn[ix, iy]
    if normals is not None:
        nd = normals @ d
        tan_t = np.sqrt(np.clip(1.0 - nd ** 2, 0.0, 1.0)) / np.maximum(np.abs(nd), 1e-3)
        tol_eff = np.clip(tol + 0.5 * span * cell * tan_t, tol, max_tol)
    else:
        tol_eff = tol
    keep = z >= zmax - tol_eff
    if cull and normals is not None:
        keep &= (normals @ d) > 0
    return keep


def visible_mask(points: np.ndarray, dirs: np.ndarray, cell: float = 4.0, tol: float = 1.5,
                 normals: np.ndarray | None = None, cull: bool = False, span: int = 3) -> np.ndarray:
    """z-buffer 방식 가시성 판정(여러 시선 방향의 합집합).

    * 과거 양산 솔리드(앞면+뒷면+리브)에서 '디자인 스킨에 해당하는 A면'만 뽑아낼 때
    * 디자인 1차 모델에서 숨은 작업용 서피스를 걸러낼 때
    두 경우 모두에 쓴다. 셀 크기 cell(mm) 격자에서 주변 span×span 셀 중 가장 앞쪽 점보다
    tol(mm) 이상 뒤에 있으면 '가려짐'. 법선이 있으면 기울어진 면은 허용오차를 자동으로 키운다.
    """
    pts = np.asarray(points, dtype=np.float64)
    keep = np.zeros(len(pts), dtype=bool)
    for d in np.atleast_2d(dirs):
        keep |= depth_visible(pts, normals, d, cell=cell, tol=tol, span=span, cull=cull)
    return keep


def solidness(points: np.ndarray, normals: np.ndarray, cell: float = 4.0, tol: float = 1.5):
    """(solid_like, normals_fixed) — 앞뒤 양면이 있는 솔리드인지 판정하고, 법선이 안쪽을 향하면 뒤집는다.

    +w 와 -w 두 방향에서 각각 보이는 점의 겹침 비율이 작으면(두 면이 다르면) 솔리드로 본다.
    """
    d = np.array([0.0, 0.0, 1.0])
    vp = depth_visible(points, normals, d, cell=cell, tol=tol)
    vn = depth_visible(points, normals, -d, cell=cell, tol=tol)
    both = float((vp & vn).sum() / max(vp.sum(), 1))
    solid_like = both < 0.5
    N = normals
    if solid_like:
        if float((normals[vp] @ d).mean()) < 0:  # 바깥 법선이어야 보이는 면의 n·d > 0
            N = -normals
    return solid_like, N, both


def stratified_subsample(groups: np.ndarray, n_target: int, rng: np.random.Generator, min_per_group: int = 150) -> np.ndarray:
    """그룹(부품) 비례 서브샘플링. 작은 부품도 최소 min_per_group개는 남긴다. 인덱스를 돌려준다."""
    n = len(groups)
    if n <= n_target:
        return np.arange(n)
    uniq, inv, counts = np.unique(groups, return_inverse=True, return_counts=True)
    quota = np.round(n_target * counts / n).astype(int)
    quota = np.minimum(counts, np.maximum(quota, min_per_group))
    out = []
    for g_idx, q in enumerate(quota):
        members = np.flatnonzero(inv == g_idx)
        if q >= len(members):
            out.append(members)
        else:
            out.append(rng.choice(members, size=int(q), replace=False))
    return np.sort(np.concatenate(out))


def random_subsample(n: int, n_target: int, rng: np.random.Generator) -> np.ndarray:
    if n <= n_target:
        return np.arange(n)
    return np.sort(rng.choice(n, size=n_target, replace=False))
