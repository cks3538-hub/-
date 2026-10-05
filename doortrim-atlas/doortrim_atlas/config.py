"""파이프라인 설정값. JSON 파일로 덮어쓸 수 있다(`--config my.json`)."""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict, fields


@dataclass
class Config:
    # ---------- 샘플링 ----------
    raw_samples: int = 250_000      # 아틀라스 구축 시 1차 표면 샘플 수(가시성 필터 전)
    atlas_points: int = 30_000      # 아틀라스에 저장할 가시(A면) 점 수
    skin_samples: int = 200_000     # 신규 스킨 1차 표면 샘플 수
    skin_points: int = 40_000       # 실제 분할 계산에 쓸 점 수
    hyp_samples: int = 60_000       # 자세(8방향) 가설 검사에 쓸 점 수

    # ---------- 가시성(z-buffer) 필터 ----------
    vis_cell_mm: float = 4.0        # 격자 셀 크기(mm)
    vis_tol_mm: float = 1.5         # 이보다 뒤에 있으면 가려진 것으로 판정(부품 두께 2.5~3mm보다 작게)
    vis_tilt_deg: float = 30.0      # 정면 외에 기울여서 보는 각도
    backface_cull: bool = True      # 솔리드로 판정되면 뒷면(법선이 반대) 점 제거
    skin_visibility: bool = True    # 신규 스킨에도 필터 적용(숨은 작업면 제거)

    # ---------- 자세 정규화 ----------
    cabin_dir: str = "auto"         # 기준 아틀라스의 '실내(캐빈) 방향'. auto | +x -x +y -y +z -z
    up_dir: str = "+z"              # 차량 좌표계에서 위쪽
    rear_dir: str = "+x"            # 차량 좌표계에서 차량 뒤쪽

    # ---------- 아틀라스 라벨 전이 ----------
    k_atlas: int = 8                # 라벨 전이에 참여할 유사 아틀라스 수
    k_nn: int = 10                  # 점당 참조할 아틀라스 이웃 점 수
    nn_sigma: float = 0.02          # 이웃 거리 가중 폭(정규화 단위, 트림 길이=1 → 0.02≈20mm)
    icp_iters: int = 8              # 정렬(ICP) 반복 횟수
    nonrigid: str = "parts"         # 비강체 보정: "parts"(부품별 국소 이동, 권장) | "grid"(격자 블록매칭) | "none"
    nonrigid_search: int = 6        # 탐색 범위(셀, 1셀≈트림길이의 1%)
    nonrigid_block: int = 12        # grid 모드 블록 크기(셀)
    partwise_min_ncc: float = 0.3   # parts 모드: 이 상관계수 미만이면 이동 안 함
    partwise_max_frac: float = 0.25 # parts 모드: 점유 셀의 이 비율보다 큰 부품은 이동 안 함

    # ---------- 융합/평활 ----------
    fusion: str = "loglinear"       # "loglinear"(전문가 곱, 권장) | "linear"(가중 평균)
    alpha_atlas: float = 0.5        # 아틀라스 확률 비중(나머지는 점 분류기)
    smooth_k: int = 10
    smooth_iters: int = 1           # 확률 평활 반복(0이면 생략). 많을수록 작은 부품이 깎임
    smooth_lambda: float = 0.5
    min_component_frac: float = 0.001  # 조각 제거 절대 하한(전체 점수 대비)
    cleanup_size_coef: float = 0.1     # 조각 제거 기준 = 그 부품의 평균 크기 × 이 계수
    piece_vote_threshold: float = 0.7  # 조각(STL 파일) 단위 다수결 적용 기준

    # ---------- 점 분류기 ----------
    clf_per_atlas: int = 6000
    clf_trees: int = 200
    clf_min_leaf: int = 2

    # ---------- 예측 ----------
    pred_k: int = 3                 # 중량 예측 시 참조할 유사 부품 수
    hidden_min_rate: float = 0.5    # 유사 차종 중 이 비율 이상이 가진 숨은 부품만 포함

    seed: int = 0

    @classmethod
    def load(cls, path: str | None) -> "Config":
        cfg = cls()
        if path:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            valid = {f.name for f in fields(cls)}
            for k, v in data.items():
                if k not in valid:
                    raise KeyError(f"알 수 없는 설정 항목: {k}")
                setattr(cfg, k, v)
        return cfg

    def dump(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, ensure_ascii=False, indent=2)

    def to_dict(self) -> dict:
        return asdict(self)
