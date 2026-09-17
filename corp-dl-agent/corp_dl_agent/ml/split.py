"""고정 그룹 분할 (SplitManifest).

- 같은 group_column 값(설계 계열/시편)은 한 split 에만 속한다 (교집합 0 을 검증).
- 약 70/15/15 는 그룹 수 비율이며 실제 행 수/비율을 manifest 에 보고한다.
- 그룹 수 < 3 이면 E_SPLIT_IMPOSSIBLE. 행 무작위 분할로 후퇴하지 않는다.
- split_policy=time: 그룹을 (그룹의 최신 시각, 그룹 ID) 순으로 정렬해 과거 그룹 → train, 이후 → val, 최신 → test.
  train 의 모든 행 시각 <= val 그룹의 최신 시각, val 의 모든 행 시각 <= test 그룹의 최신 시각 (미래 정보 누수 없음).
- manifest 에 data/split/taskspec/code/lock hash 를 기록한다 (lock 이 없으면 "UNLOCKED").
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field, ValidationError, field_validator, model_validator

from corp_dl_agent.common import (
    StrictModel,
    atomic_write_json,
    canonical_json,
    now_iso,
    read_json,
    sha256_text,
    validate_strict,
)
from corp_dl_agent.errors import AgentError, blocked_dependency
from corp_dl_agent.ml import normalize_lock_hash
from corp_dl_agent.ml.taskspec import TaskSpec, taskspec_hash

if TYPE_CHECKING:
    import pandas as pd

SPLIT_NAMES: tuple[str, str, str] = ("train", "val", "test")
DEFAULT_RATIOS: tuple[float, float, float] = (0.7, 0.15, 0.15)
MIN_GROUPS = 3


class SplitManifest(StrictModel):
    policy: Literal["group", "time"]
    seed: int
    ratios_requested: list[float]
    id_column: str
    group_column: str
    time_column: str | None = None
    n_groups: dict[str, int]
    n_rows: dict[str, int]
    row_fractions: dict[str, float]
    ids: dict[str, list[str]]
    groups: dict[str, list[str]]
    hashes: dict[str, str]
    overlap_checked: bool
    time_boundaries: dict[str, str] = Field(default_factory=dict)
    data_origin: Literal["synthetic", "corporate"]
    synthetic: bool
    created_at: str

    @field_validator("ratios_requested")
    @classmethod
    def _ratios(cls, v: list[float]) -> list[float]:
        if len(v) != 3 or any(x <= 0 for x in v) or abs(sum(v) - 1.0) > 1e-6:
            raise ValueError("ratios_requested 는 합이 1 인 양수 3개(train, val, test) 여야 합니다")
        return v

    @model_validator(mode="after")
    def _split_keys(self) -> SplitManifest:
        for name, d in (
            ("n_groups", self.n_groups),
            ("n_rows", self.n_rows),
            ("row_fractions", self.row_fractions),
            ("ids", self.ids),
            ("groups", self.groups),
        ):
            if set(d.keys()) != set(SPLIT_NAMES):
                raise ValueError(f"{name} 의 키는 정확히 {list(SPLIT_NAMES)} 이어야 합니다")
        for key in ("data", "split", "taskspec", "code", "lock"):
            if key not in self.hashes or not self.hashes[key]:
                raise ValueError(f"hashes.{key} 가 필요합니다")
        return self

    def all_ids(self) -> list[str]:
        return [i for name in SPLIT_NAMES for i in self.ids[name]]

    def total_rows(self) -> int:
        return sum(self.n_rows.values())


def _pd() -> Any:
    try:
        import pandas as pd
    except ImportError as exc:  # pragma: no cover
        raise blocked_dependency("pandas", "데이터 분할") from exc
    return pd


def allocate_group_counts(n_groups: int, ratios: Sequence[float] = DEFAULT_RATIOS) -> tuple[int, int, int]:
    """그룹 수를 train/val/test 로 배분. val/test 는 최소 1 그룹, train 은 나머지 (1 미만이면 불가)."""
    if n_groups < MIN_GROUPS:
        raise AgentError(
            "E_SPLIT_IMPOSSIBLE",
            f"그룹 수가 {n_groups}개라 train/val/test 로 나눌 수 없습니다 (최소 {MIN_GROUPS}개 필요)",
            details={"n_groups": n_groups, "min_groups": MIN_GROUPS},
        )
    n_val = max(1, round(n_groups * ratios[1]))
    n_test = max(1, round(n_groups * ratios[2]))
    n_train = n_groups - n_val - n_test
    if n_train < 1:
        raise AgentError(
            "E_SPLIT_IMPOSSIBLE", "train 에 배정할 그룹이 없습니다", details={"n_groups": n_groups}
        )
    return n_train, n_val, n_test


def dataframe_hash(df: pd.DataFrame) -> str:
    """DataFrame 내용 hash (CSV 직렬화 기준). 파일 hash 가 있으면 그 값을 우선 사용한다."""
    text = df.to_csv(index=False, lineterminator="\n")
    return sha256_text(text)


def split_hash(policy: str, seed: int, ids: dict[str, list[str]]) -> str:
    return sha256_text(canonical_json({"policy": policy, "seed": seed, "ids": ids}))


def _time_series(df: pd.DataFrame, column: str) -> Any:
    pd = _pd()
    s = df[column]
    if not pd.api.types.is_datetime64_any_dtype(s):
        s = pd.to_datetime(s.astype(str).str.strip(), errors="coerce", format="ISO8601")
    if s.isna().any():
        bad = [int(i) for i in df.index[s.isna().to_numpy()][:20]]
        raise AgentError(
            "E_INPUT_INVALID",
            f"시간 열 '{column}' 에 해석할 수 없는 값이 있어 시간 분할을 할 수 없습니다",
            details={"column": column, "rows": bad, "count": int(s.isna().sum())},
        )
    return s


def group_split(
    df: pd.DataFrame,
    spec: TaskSpec,
    *,
    code_hash: str,
    lock_hash: str | None = None,
    data_hash: str | None = None,
    ratios: Sequence[float] = DEFAULT_RATIOS,
) -> SplitManifest:
    """TaskSpec.split_policy 에 따라 고정 그룹 분할 manifest 를 만든다 (데이터는 수정하지 않는다)."""
    for col in (spec.id_column, spec.group_column):
        if col not in df.columns:
            raise AgentError("E_INPUT_INVALID", f"분할에 필요한 열이 없습니다: {col}")
    if len(ratios) != 3 or abs(sum(ratios) - 1.0) > 1e-6 or any(r <= 0 for r in ratios):
        raise AgentError(
            "E_SCHEMA_INVALID", "분할 비율은 합이 1 인 양수 3개여야 합니다", details={"ratios": list(ratios)}
        )
    ids = df[spec.id_column].astype(str)
    groups = df[spec.group_column].astype(str)
    if (groups.str.strip() == "").any():
        raise AgentError("E_INPUT_INVALID", f"그룹 열 '{spec.group_column}' 에 빈 값이 있습니다")
    if ids.duplicated().any():
        raise AgentError(
            "E_INPUT_INVALID", f"ID 열 '{spec.id_column}' 에 중복이 있어 분할 manifest 를 만들 수 없습니다"
        )
    unique_groups = sorted(set(groups.tolist()))
    n_train, n_val, n_test = allocate_group_counts(len(unique_groups), ratios)

    time_boundaries: dict[str, str] = {}
    if spec.split_policy == "time":
        if spec.time_column is None or spec.time_column not in df.columns:
            raise AgentError(
                "E_INPUT_INVALID",
                "split_policy=time 에는 데이터에 time_column 이 있어야 합니다",
                details={"time_column": spec.time_column},
            )
        times = _time_series(df, spec.time_column)
        group_max = times.groupby(groups.to_numpy()).max()
        order = sorted(unique_groups, key=lambda g: (group_max[g], g))
    else:
        order = list(unique_groups)
        random.Random(spec.seed).shuffle(order)  # noqa: S311 - 결정적 분할용 seed, 보안 목적 아님

    assigned = {
        "train": order[:n_train],
        "val": order[n_train : n_train + n_val],
        "test": order[n_train + n_val :],
    }
    masks = {name: groups.isin(assigned[name]).to_numpy() for name in SPLIT_NAMES}
    id_lists = {name: ids[masks[name]].tolist() for name in SPLIT_NAMES}

    # 교집합 0 검증 (그룹·ID·행 배정 완전성)
    for a, b in (("train", "val"), ("train", "test"), ("val", "test")):
        g_overlap = set(assigned[a]) & set(assigned[b])
        i_overlap = set(id_lists[a]) & set(id_lists[b])
        if g_overlap or i_overlap:
            raise AgentError(
                "E_LEAKAGE",
                f"{a}/{b} 사이에 그룹 또는 ID 가 겹칩니다",
                details={"groups": sorted(g_overlap)[:10], "ids": sorted(i_overlap)[:10]},
            )
    if sum(len(v) for v in id_lists.values()) != len(df):
        raise AgentError("E_INTERNAL", "분할 행 수 합이 전체 행 수와 다릅니다")

    if spec.split_policy == "time":
        assert spec.time_column is not None
        times = _time_series(df, spec.time_column)
        tmax = {name: times[masks[name]].max() for name in SPLIT_NAMES}
        tmin = {name: times[masks[name]].min() for name in SPLIT_NAMES}
        group_max = times.groupby(groups.to_numpy()).max()
        val_group_max_min = min(group_max[g] for g in assigned["val"])
        test_group_max_min = min(group_max[g] for g in assigned["test"])
        if tmax["train"] > val_group_max_min or tmax["val"] > test_group_max_min:
            raise AgentError(
                "E_LEAKAGE",
                "시간 분할에서 미래 정보가 train/val 에 포함됩니다",
                details={"train_max": str(tmax["train"]), "val_max": str(tmax["val"])},
            )
        time_boundaries = {
            "train_time_min": tmin["train"].isoformat(),
            "train_time_max": tmax["train"].isoformat(),
            "val_time_min": tmin["val"].isoformat(),
            "val_time_max": tmax["val"].isoformat(),
            "test_time_min": tmin["test"].isoformat(),
            "test_time_max": tmax["test"].isoformat(),
            "rule": "train_time_max <= min(val group max); val_time_max <= min(test group max)",
        }

    n_rows = {name: len(id_lists[name]) for name in SPLIT_NAMES}
    total = len(df)
    manifest = SplitManifest(
        policy=spec.split_policy,
        seed=spec.seed,
        ratios_requested=[float(r) for r in ratios],
        id_column=spec.id_column,
        group_column=spec.group_column,
        time_column=spec.time_column,
        n_groups={name: len(assigned[name]) for name in SPLIT_NAMES},
        n_rows=n_rows,
        row_fractions={name: (n_rows[name] / total if total else 0.0) for name in SPLIT_NAMES},
        ids=id_lists,
        groups={name: list(assigned[name]) for name in SPLIT_NAMES},
        hashes={
            "data": data_hash or dataframe_hash(df),
            "split": split_hash(spec.split_policy, spec.seed, id_lists),
            "taskspec": taskspec_hash(spec),
            "code": code_hash,
            "lock": normalize_lock_hash(lock_hash),
        },
        overlap_checked=True,
        time_boundaries=time_boundaries,
        data_origin=spec.data_origin,
        synthetic=spec.data_origin == "synthetic",
        created_at=now_iso(),
    )
    return manifest


def save_split(manifest: SplitManifest, path: str | Path) -> Path:
    p = Path(path)
    atomic_write_json(p, manifest.model_dump(mode="json"))
    return p


def load_split(path: str | Path) -> SplitManifest:
    p = Path(path)
    if not p.is_file():
        raise AgentError(
            "E_INPUT_INVALID", f"split manifest 파일이 없습니다: {p.name}", details={"path": str(p)}
        )
    try:
        data = read_json(p)
        manifest: SplitManifest = validate_strict(SplitManifest, data)
    except ValidationError as exc:
        raise AgentError(
            "E_SCHEMA_INVALID",
            f"split manifest 검증 실패: {p.name}",
            details={"errors": [str(e.get("loc")) + ": " + str(e.get("msg")) for e in exc.errors()][:20]},
        ) from exc
    except ValueError as exc:
        raise AgentError(
            "E_SCHEMA_INVALID", f"split manifest JSON 파싱 실패: {p.name}", details={"error": str(exc)[:300]}
        ) from exc
    return manifest


def apply_split(df: pd.DataFrame, manifest: SplitManifest) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """manifest 의 ID 목록대로 (train, val, test) 를 돌려준다. 데이터와 manifest 가 불일치하면 실패."""
    if manifest.id_column not in df.columns:
        raise AgentError("E_INPUT_INVALID", f"데이터에 ID 열 '{manifest.id_column}' 이 없습니다")
    ids = df[manifest.id_column].astype(str)
    if ids.duplicated().any():
        raise AgentError("E_INPUT_INVALID", "데이터 ID 가 중복되어 split 을 적용할 수 없습니다")
    data_ids = set(ids.tolist())
    manifest_ids = set(manifest.all_ids())
    missing = sorted(manifest_ids - data_ids)
    extra = sorted(data_ids - manifest_ids)
    if missing or extra:
        raise AgentError(
            "E_INPUT_INVALID",
            "split manifest 와 데이터의 ID 가 일치하지 않습니다 (데이터가 바뀌었거나 다른 파일입니다)",
            details={
                "missing_in_data": missing[:20],
                "not_in_manifest": extra[:20],
                "n_missing": len(missing),
                "n_extra": len(extra),
            },
        )
    if manifest.group_column in df.columns:
        groups = df[manifest.group_column].astype(str)
        seen: dict[str, str] = {}
        for name in SPLIT_NAMES:
            for g in set(groups[ids.isin(manifest.ids[name])].tolist()):
                if g in seen and seen[g] != name:
                    raise AgentError("E_LEAKAGE", f"그룹 '{g}' 이 {seen[g]}/{name} 양쪽에 있습니다")
                seen[g] = name
    parts = []
    for name in SPLIT_NAMES:
        mask = ids.isin(manifest.ids[name]).to_numpy()
        parts.append(df[mask].reset_index(drop=True))
    return parts[0], parts[1], parts[2]


def verify_manifest_hashes(
    manifest: SplitManifest,
    *,
    data_hash: str | None = None,
    taskspec: TaskSpec | None = None,
    code_hash: str | None = None,
) -> list[str]:
    """저장된 manifest 와 현재 값의 hash 불일치 항목을 돌려준다 (비어 있으면 일치)."""
    mismatched: list[str] = []
    if data_hash is not None and manifest.hashes["data"] != data_hash:
        mismatched.append("data")
    if taskspec is not None and manifest.hashes["taskspec"] != taskspec_hash(taskspec):
        mismatched.append("taskspec")
    if code_hash is not None and manifest.hashes["code"] != code_hash:
        mismatched.append("code")
    if manifest.hashes["split"] != split_hash(manifest.policy, manifest.seed, manifest.ids):
        mismatched.append("split")
    return mismatched
