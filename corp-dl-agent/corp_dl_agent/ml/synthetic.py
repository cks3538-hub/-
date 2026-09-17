"""합성 클립(clip) 삽입력/유지력 데이터 생성기 (결정적, seed 고정).

- 이 생성기는 실제 차량 부품/재료 물리를 대표하지 않는다. 비선형 + 잡음 + 그룹(설계 계열) 효과를 가진 임의의 합성 함수다.
- 열: specimen_id(문자열, 선행 0 보존), group_id(설계 계열), 두께/홀 직경/각도/탄성계수/마찰/온도/삽입속도,
  material_family/data_source(범주), design_revision(메타), measured_at(ISO 시각), target, post_* (사후 결과 → excluded).
- write_fixtures(dir) 는 fixtures/ml/{clip_regression.csv, clip_classification.csv, task_regression.yaml, task_classification.yaml, README.md}
  를 만든다 (분류 CSV 는 UTF-8-SIG, 회귀 CSV 는 UTF-8 로 저장하여 인코딩 검사 경로를 모두 시험한다).
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from corp_dl_agent.common import atomic_write_bytes, atomic_write_text
from corp_dl_agent.errors import AgentError, blocked_dependency

if TYPE_CHECKING:
    import pandas as pd

ID_COLUMN = "specimen_id"
GROUP_COLUMN = "group_id"
TIME_COLUMN = "measured_at"
NUMERIC_FEATURES: tuple[str, ...] = (
    "thickness_mm",
    "hole_diameter_mm",
    "angle_deg",
    "elastic_modulus_mpa",
    "friction_coef",
    "temperature_c",
    "insertion_speed_mm_s",
)
CATEGORICAL_FEATURES: tuple[str, ...] = ("material_family", "data_source")
REGRESSION_TARGET = "insertion_force_n"
CLASSIFICATION_TARGET = "retention_pass"
POST_COLUMN = "post_retention_force_n"  # 사후(시험 후) 측정값: 추론 시점에 알 수 없음 → excluded
META_COLUMNS: tuple[str, ...] = ("design_revision",)
UNITS: dict[str, str] = {
    "thickness_mm": "mm",
    "hole_diameter_mm": "mm",
    "angle_deg": "deg",
    "elastic_modulus_mpa": "MPa",
    "friction_coef": "1",
    "temperature_c": "degC",
    "insertion_speed_mm_s": "mm/s",
    REGRESSION_TARGET: "N",
}
MATERIALS: dict[str, float] = {"PA66": 2800.0, "POM": 2600.0, "PP": 1400.0}
DISCLAIMER_KO = "이 데이터는 합성(synthetic) 데이터이며 실제 차량 부품·재료 물리를 대표하지 않습니다. 회사 자료를 이름만 바꾼 것이 아닙니다."
GENERATOR_VERSION = "clip-synthetic/1.0"


def _np() -> Any:
    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover
        raise blocked_dependency("numpy", "합성 데이터 생성") from exc
    return np


def _pd() -> Any:
    try:
        import pandas as pd
    except ImportError as exc:  # pragma: no cover
        raise blocked_dependency("pandas", "합성 데이터 생성") from exc
    return pd


def generate_clip_dataset(n_rows: int, n_groups: int, seed: int, task_type: str) -> pd.DataFrame:
    """결정적 합성 데이터. task_type: regression -> insertion_force_n, binary_classification -> retention_pass."""
    if task_type not in ("regression", "binary_classification"):
        raise AgentError("E_SCHEMA_INVALID", f"알 수 없는 task_type: {task_type}")
    if n_rows < 3 or n_groups < 1 or n_groups > n_rows:
        raise AgentError(
            "E_SCHEMA_INVALID",
            "n_rows >= 3, 1 <= n_groups <= n_rows 이어야 합니다",
            details={"n_rows": n_rows, "n_groups": n_groups},
        )
    np = _np()
    pd = _pd()
    rng = np.random.default_rng(seed)
    mats = list(MATERIALS)

    # 그룹(설계 계열) 수준 기본값 — 같은 계열은 비슷한 형상/재료/시험 시기를 가진다
    g_thick = rng.uniform(1.5, 3.5, n_groups)
    g_hole = rng.uniform(5.0, 9.0, n_groups)
    g_angle = rng.uniform(15.0, 45.0, n_groups)
    g_mat = rng.integers(0, len(mats), n_groups)
    g_offset = rng.normal(0.0, 1.0, n_groups)
    g_offset2 = rng.normal(0.0, 1.5, n_groups)
    g_day = rng.integers(0, 365, n_groups)
    g_rev = rng.integers(1, 4, n_groups)

    # 행별 그룹 배정: 모든 그룹이 최소 1행을 갖도록 앞 n_groups 행을 순서대로, 나머지는 무작위
    grp = np.concatenate([np.arange(n_groups), rng.integers(0, n_groups, n_rows - n_groups)])
    rng.shuffle(grp)

    thickness = np.clip(g_thick[grp] + rng.normal(0.0, 0.15, n_rows), 1.0, 4.0)
    hole = np.clip(g_hole[grp] + rng.normal(0.0, 0.2, n_rows), 4.0, 10.0)
    angle = np.clip(g_angle[grp] + rng.normal(0.0, 2.0, n_rows), 10.0, 50.0)
    mat_idx = g_mat[grp]
    material = np.asarray([mats[i] for i in mat_idx])
    modulus = np.asarray([MATERIALS[m] for m in material]) * rng.uniform(0.9, 1.1, n_rows)
    friction = rng.uniform(0.15, 0.45, n_rows)
    temperature = rng.uniform(-30.0, 80.0, n_rows)
    speed = rng.uniform(5.0, 50.0, n_rows)
    source = np.where(rng.uniform(0, 1, n_rows) < 0.7, "test", "cae")
    day = g_day[grp] + rng.integers(0, 20, n_rows)
    hours = rng.integers(0, 24, n_rows)
    measured_at = [_iso_day(int(d), int(h)) for d, h in zip(day, hours, strict=True)]

    stiff = modulus / 2800.0
    angle_rad = np.deg2rad(angle)
    # 임의의 비선형 합성 함수 (물리 모델 아님)
    force = (
        12.0
        * (thickness / 2.5) ** 1.6
        * (6.5 / hole) ** 0.9
        * (1.0 + 1.8 * friction)
        * stiff**0.5
        * (1.0 + 0.6 * np.sin(angle_rad))
        * (1.0 + 0.15 * np.log(speed / 20.0))
        * (1.0 - 0.004 * (temperature - 23.0))
        + g_offset[grp]
        + rng.normal(0.0, 1.2, n_rows)
    )
    force = np.clip(force, 0.5, None)
    retention = (
        25.0
        * (thickness / 2.5) ** 1.2
        * (6.5 / hole) ** 1.5
        * stiff**0.7
        * (1.0 + friction)
        * (1.0 - 0.003 * (temperature - 23.0))
        * (1.0 - 0.3 * np.cos(angle_rad) ** 2)
        + g_offset2[grp]
        + rng.normal(0.0, 1.5, n_rows)
    )
    margin = retention - 22.0
    p_pass = 1.0 / (1.0 + np.exp(-margin / 1.5))
    retention_pass = (rng.uniform(0, 1, n_rows) < p_pass).astype("int64")
    post_retention = retention + rng.normal(0.0, 0.3, n_rows)

    width = max(6, len(str(n_rows)))
    df = pd.DataFrame(
        {
            ID_COLUMN: [str(i + 1).zfill(width) for i in range(n_rows)],
            GROUP_COLUMN: [f"FAM-{int(g) + 1:03d}" for g in grp],
            "thickness_mm": thickness,
            "hole_diameter_mm": hole,
            "angle_deg": angle,
            "elastic_modulus_mpa": modulus,
            "friction_coef": friction,
            "temperature_c": temperature,
            "insertion_speed_mm_s": speed,
            "material_family": material,
            "data_source": source,
            "design_revision": [f"R{int(g_rev[g])}" for g in grp],
            TIME_COLUMN: measured_at,
        }
    )
    if task_type == "regression":
        df[REGRESSION_TARGET] = force
    else:
        df[CLASSIFICATION_TARGET] = retention_pass
    df[POST_COLUMN] = post_retention
    return df


def _iso_day(day: int, hour: int) -> str:
    d = date(2024, 1, 1) + timedelta(days=day)
    return f"{d.isoformat()}T{hour:02d}:00:00+00:00"


def _taskspec_dict(task_type: str, data_file: str, *, seed: int) -> dict[str, Any]:
    target = REGRESSION_TARGET if task_type == "regression" else CLASSIFICATION_TARGET
    units = {k: v for k, v in UNITS.items() if k in NUMERIC_FEATURES or k == target}
    return {
        "task_id": "clip_insertion_force" if task_type == "regression" else "clip_retention_pass",
        "task_type": task_type,
        "data_path": data_file,
        "target": target,
        "numeric_features": list(NUMERIC_FEATURES),
        "categorical_features": list(CATEGORICAL_FEATURES),
        "units": units,
        "id_column": ID_COLUMN,
        "group_column": GROUP_COLUMN,
        "split_policy": "group" if task_type == "regression" else "time",
        "time_column": TIME_COLUMN,
        "seed": seed,
        "metric": "mae" if task_type == "regression" else "average_precision",
        "direction": "min" if task_type == "regression" else "max",
        "acceptance": None,
        "resource_budget": {"mode": "demo"},
        "data_origin": "synthetic",
        "purpose": (
            "합성 클립 삽입력 회귀 demo (실제 차량 물리 아님)"
            if task_type == "regression"
            else "합성 클립 유지력 합격 분류 demo (실제 차량 물리 아님)"
        ),
        "excluded_columns": [POST_COLUMN, *META_COLUMNS],
    }


def _readme(n_rows: int, n_groups: int) -> str:
    return f"""# fixtures/ml — 합성 클립 데이터 (synthetic: true)

{DISCLAIMER_KO}

생성기: `corp_dl_agent.ml.synthetic.write_fixtures` ({GENERATOR_VERSION}), 결정적(seed 고정). 재생성:

```
.venv/bin/python -c "from corp_dl_agent.ml.synthetic import write_fixtures; write_fixtures('fixtures/ml')"
```

| 파일 | 내용 |
|---|---|
| `clip_regression.csv` | {n_rows}행 / {n_groups} 설계 계열(group), target `{REGRESSION_TARGET}` (N), UTF-8 |
| `clip_classification.csv` | {n_rows}행 / {n_groups} 설계 계열, target `{CLASSIFICATION_TARGET}` (0/1), UTF-8-SIG(BOM) |
| `task_regression.yaml` | 회귀 TaskSpec (group split, metric mae, acceptance 없음 → NEEDS_ACCEPTANCE_CRITERIA) |
| `task_classification.yaml` | 분류 TaskSpec (time split, metric average_precision, acceptance 없음) |

열: `{ID_COLUMN}` (문자열 ID, 선행 0 보존), `{GROUP_COLUMN}` (설계 계열; 같은 계열은 한 split 에만),
숫자 feature {list(NUMERIC_FEATURES)}, 범주 feature {list(CATEGORICAL_FEATURES)} (`data_source` 는 시험/CAE 출처 구분),
`design_revision` (메타, feature 아님), `{TIME_COLUMN}` (ISO 8601, 시간 분할용),
`{POST_COLUMN}` (시험 후 측정한 사후 결과 → 추론 시점에 알 수 없으므로 `excluded_columns`).

target 은 두께·홀 직경·각도·탄성계수·마찰·온도·삽입속도의 **임의 비선형 함수 + 잡음 + 계열 효과** 로 만든 값이며
물리 법칙이나 실제 시험 결과와 무관합니다. 이 자료로 만든 모델은 `data_origin: synthetic` 표시를 가지며 운영 판단에 자동 사용되지 않습니다.
"""


def write_fixtures(
    directory: str | Path, *, n_rows: int = 600, n_groups: int = 40, seed: int = 42
) -> dict[str, Path]:
    """fixtures/ml 파일 일체를 결정적으로 생성한다. 반환: 이름 -> 경로."""
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    out: dict[str, Path] = {}
    reg = generate_clip_dataset(n_rows, n_groups, seed, "regression")
    cls = generate_clip_dataset(n_rows, n_groups, seed + 1, "binary_classification")
    reg_csv = reg.to_csv(index=False, lineterminator="\n", float_format="%.6g")
    cls_csv = cls.to_csv(index=False, lineterminator="\n", float_format="%.6g")
    atomic_write_bytes(d / "clip_regression.csv", reg_csv.encode("utf-8"))
    atomic_write_bytes(d / "clip_classification.csv", cls_csv.encode("utf-8-sig"))
    out["clip_regression.csv"] = d / "clip_regression.csv"
    out["clip_classification.csv"] = d / "clip_classification.csv"
    for name, task_type, data_file in (
        ("task_regression.yaml", "regression", "clip_regression.csv"),
        ("task_classification.yaml", "binary_classification", "clip_classification.csv"),
    ):
        spec = _taskspec_dict(task_type, data_file, seed=seed)
        header = f"# 합성 TaskSpec (synthetic). {DISCLAIMER_KO}\n"
        atomic_write_text(d / name, header + yaml.safe_dump(spec, allow_unicode=True, sort_keys=False))
        out[name] = d / name
    atomic_write_text(d / "README.md", _readme(n_rows, n_groups))
    out["README.md"] = d / "README.md"
    return out


def fixture_dir() -> Path:
    """저장소의 fixtures/ml 폴더 (설치 후에는 존재하지 않을 수 있다)."""
    return Path(__file__).resolve().parents[2] / "fixtures" / "ml"


def approx_equal(a: float, b: float, rel: float = 1e-9) -> bool:
    return math.isclose(a, b, rel_tol=rel, abs_tol=1e-12)
