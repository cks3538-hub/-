"""자세(pose) 정규화: 임의 좌표계의 도어트림을 공통 좌표계(u,v,w)로 옮긴다.

공통 좌표계 정의
  u : 차량 앞(0) → 뒤(1)         (트림의 가장 긴 방향, 0~1로 정규화)
  v : 아래(0) → 위(1)            (두 번째 긴 방향, 0~1로 정규화)
  w : 실내(캐빈)쪽이 +, 바탕 패널면이 0, 단위는 '트림 길이'(u와 같은 척도)

좌/우 도어는 거울상이므로, 필요하면 반사(det=-1)까지 허용하여 모두 같은 배치로 맞춘다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

import numpy as np
from scipy.stats import skew

_AXIS = {
    "+x": np.array([1.0, 0, 0]), "-x": np.array([-1.0, 0, 0]),
    "+y": np.array([0, 1.0, 0]), "-y": np.array([0, -1.0, 0]),
    "+z": np.array([0, 0, 1.0]), "-z": np.array([0, 0, -1.0]),
}


def pca_axes(points: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """중심과 주축(분산 큰 순서, 행 벡터)과 고유값을 돌려준다."""
    c = points.mean(axis=0)
    X = points - c
    cov = X.T @ X / max(len(X), 1)
    evals, evecs = np.linalg.eigh(cov)
    order = np.argsort(evals)[::-1]
    return c, evecs[:, order].T.copy(), evals[order]


def heuristic_rotation(points: np.ndarray, axes: np.ndarray, cabin_dir: str = "auto",
                       up_dir: str = "+z", rear_dir: str = "+x") -> Tuple[np.ndarray, str]:
    """차량 좌표계 관례(X 뒤, Z 위, 캐빈은 y=0 쪽)로 주축의 부호를 정한다.

    돌려주는 R 의 행은 (u, v, w) 단위 벡터. 설명 문자열도 함께 돌려준다.
    """
    e1, e2, e3 = axes
    u = e1 if e1 @ _AXIS[rear_dir] >= 0 else -e1
    v = e2 if e2 @ _AXIS[up_dir] >= 0 else -e2
    if cabin_dir != "auto":
        tv = _AXIS[cabin_dir]
        w = e3 if e3 @ tv >= 0 else -e3
        how = f"cabin_dir={cabin_dir}"
    else:
        c = points.mean(axis=0)
        length = float(np.ptp(points @ u))
        if abs(c[1]) > 0.25 * length:
            # 차량 좌표계: 트림이 y=±700 근처에 있고 실내는 y=0 쪽
            tv = np.array([0.0, -np.sign(c[1]), 0.0])
            w = e3 if e3 @ tv >= 0 else -e3
            how = f"car-coordinate(centroid y={c[1]:.0f})"
        else:
            s = float(skew(points @ e3))
            w = e3 if s >= 0 else -e3
            how = f"skewness({s:+.2f})"
    return np.vstack([u, v, w]), how


def sign_hypotheses(R: np.ndarray) -> List[np.ndarray]:
    """(±u, ±v, ±w) 8가지 부호 조합. 첫 번째가 입력 R 그대로."""
    u, v, w = R
    out = []
    for su in (1, -1):
        for sv in (1, -1):
            for sw in (1, -1):
                out.append(np.vstack([su * u, sv * v, sw * w]))
    return out


@dataclass
class Frame:
    """mm 좌표 ↔ 정규화 좌표 변환."""
    center: np.ndarray
    R: np.ndarray
    umin: float
    su: float
    vmin: float
    sv: float
    w0: float

    @staticmethod
    def fit(center: np.ndarray, R: np.ndarray, rotated_visible: np.ndarray) -> "Frame":
        q = rotated_visible
        umin, umax = np.percentile(q[:, 0], [0.5, 99.5])
        vmin, vmax = np.percentile(q[:, 1], [0.5, 99.5])
        w0 = float(np.percentile(q[:, 2], 15))  # 바탕 패널 높이(대부분의 점이 모여 있는 곳)
        return Frame(np.asarray(center, float), np.asarray(R, float),
                     float(umin), float(max(umax - umin, 1e-6)), float(vmin), float(max(vmax - vmin, 1e-6)), w0)

    # --- mm → 회전만 적용 ---
    def rotate(self, p: np.ndarray) -> np.ndarray:
        return (np.asarray(p, float) - self.center) @ self.R.T

    def rotate_vec(self, n: np.ndarray) -> np.ndarray:
        return np.asarray(n, float) @ self.R.T

    # --- 회전 좌표 → 정규화 ---
    def from_rotated(self, q: np.ndarray) -> np.ndarray:
        q = np.asarray(q, float)
        return np.column_stack([(q[:, 0] - self.umin) / self.su,
                                (q[:, 1] - self.vmin) / self.sv,
                                (q[:, 2] - self.w0) / self.su])

    def normalize(self, p: np.ndarray) -> np.ndarray:
        return self.from_rotated(self.rotate(p))

    def to_rotated(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, float)
        return np.column_stack([x[:, 0] * self.su + self.umin,
                                x[:, 1] * self.sv + self.vmin,
                                x[:, 2] * self.su + self.w0])

    def denormalize(self, x: np.ndarray) -> np.ndarray:
        return self.to_rotated(x) @ self.R + self.center

    @property
    def mirrored(self) -> bool:
        return bool(np.linalg.det(self.R) < 0)

    def to_dict(self) -> dict:
        return {"center": self.center.tolist(), "R": self.R.tolist(), "umin": self.umin, "su": self.su,
                "vmin": self.vmin, "sv": self.sv, "w0": self.w0}

    @staticmethod
    def from_dict(d: dict) -> "Frame":
        return Frame(np.array(d["center"], float), np.array(d["R"], float), d["umin"], d["su"], d["vmin"], d["sv"], d["w0"])
