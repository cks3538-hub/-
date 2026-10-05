"""결과 시각화: PNG 미리보기, 색상 PLY, 부품별 STL."""
from __future__ import annotations

import os
from typing import Dict, List, Optional, Sequence

import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402
import trimesh  # noqa: E402
import warnings  # noqa: E402

warnings.filterwarnings("ignore", message="Glyph .* missing from font")


def _setup_font() -> bool:
    """한글 글꼴이 있으면 설정하고 True. (Windows: 맑은 고딕, Linux: 나눔고딕 등)"""
    available = {f.name for f in font_manager.fontManager.ttflist}
    for name in ("Malgun Gothic", "NanumGothic", "NanumBarunGothic", "AppleGothic", "Noto Sans CJK KR", "Noto Sans KR"):
        if name in available:
            plt.rcParams["font.family"] = name
            plt.rcParams["axes.unicode_minus"] = False
            return True
    return False


KOREAN_FONT = _setup_font()


def _t(ko: str, en: str) -> str:
    return ko if KOREAN_FONT else en


def label(ko: str, en: str) -> str:
    """그림 제목용: 한글 글꼴이 있으면 한글, 없으면 영문."""
    return _t(ko, en)


def code_colors(n: int) -> np.ndarray:
    cmaps = [plt.get_cmap("tab20"), plt.get_cmap("tab20b"), plt.get_cmap("tab20c")]
    cols = []
    for i in range(n):
        cm = cmaps[(i // 20) % len(cmaps)]
        cols.append(cm((i % 20) / 20.0))
    return np.array(cols)


def preview_png(P: np.ndarray, labels: np.ndarray, codes: Sequence[str], path: str, title: str = "",
                truth: Optional[np.ndarray] = None, point_size: float = 1.5) -> None:
    """정규화 좌표의 점을 정면(u-v)과 평면(u-w)으로 그린다. truth 가 있으면 비교용으로 함께 그린다."""
    cols = code_colors(len(codes))
    present = np.unique(np.concatenate([labels, truth]) if truth is not None else labels)
    ncol = 2 if truth is not None else 1
    fig, axes = plt.subplots(2, ncol, figsize=(7 * ncol + 2, 8), squeeze=False)
    first = _t("예측", "prediction") if truth is not None else _t("분할 결과", "segmentation")
    panels = [(labels, first)] + ([(truth, _t("정답", "ground truth"))] if truth is not None else [])
    for c, (lab, name) in enumerate(panels):
        axes[0, c].scatter(P[:, 0], P[:, 1], c=cols[lab], s=point_size, linewidths=0)
        axes[0, c].set_aspect("equal"); axes[0, c].set_title(f"{name} - " + _t("정면(u: 앞→뒤, v: 아래→위)", "front view (u: front→rear, v: bottom→top)"))
        axes[1, c].scatter(P[:, 0], P[:, 2], c=cols[lab], s=point_size, linewidths=0)
        axes[1, c].set_aspect("equal"); axes[1, c].set_title(f"{name} - " + _t("위에서 본 깊이(u, w: 실내쪽 +)", "top view depth (u, w: cabin side +)"))
        for ax in axes[:, c]:
            ax.set_xlim(-0.05, 1.05)
    handles = [plt.Line2D([0], [0], marker="o", color="w", markerfacecolor=cols[i], markersize=8, label=codes[i]) for i in present]
    fig.legend(handles=handles, loc="center right", fontsize=8, frameon=False)
    fig.suptitle(title)
    fig.tight_layout(rect=(0, 0, 0.86, 0.96))
    fig.savefig(path, dpi=130)
    plt.close(fig)


def export_colored_mesh(meshes: List[trimesh.Trimesh], face_labels: List[np.ndarray], codes: Sequence[str], path: str) -> None:
    cols = (code_colors(len(codes))[:, :3] * 255).astype(np.uint8)
    parts = []
    for m, fl in zip(meshes, face_labels):
        mm = m.copy()
        fc = np.column_stack([cols[fl], np.full(len(fl), 255, np.uint8)])
        mm.visual = trimesh.visual.ColorVisuals(mm, face_colors=fc)
        parts.append(mm)
    merged = trimesh.util.concatenate(parts) if len(parts) > 1 else parts[0]
    merged.export(path)


def export_part_meshes(meshes: List[trimesh.Trimesh], face_labels: List[np.ndarray], codes: Sequence[str], out_dir: str) -> Dict[str, str]:
    os.makedirs(out_dir, exist_ok=True)
    out = {}
    for ci, code in enumerate(codes):
        subs = []
        for m, fl in zip(meshes, face_labels):
            sel = np.flatnonzero(fl == ci)
            if len(sel):
                subs.append(m.submesh([sel], append=True))
        if subs:
            merged = trimesh.util.concatenate(subs) if len(subs) > 1 else subs[0]
            safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in code)
            p = os.path.join(out_dir, f"{safe}.stl")
            merged.export(p)
            out[code] = p
    return out
