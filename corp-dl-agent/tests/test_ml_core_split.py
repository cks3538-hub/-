"""고정 그룹 분할 시험: 교집합 0, 실제 행 비율 보고, 그룹 부족 시 실패(행 분할 금지), 시간 누수 방지, manifest hash/저장/적용."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from corp_dl_agent.errors import AgentError
from corp_dl_agent.ml import compute_code_hash
from corp_dl_agent.ml.data import load_dataset
from corp_dl_agent.ml.split import (
    SplitManifest,
    allocate_group_counts,
    apply_split,
    group_split,
    load_split,
    save_split,
    verify_manifest_hashes,
)
from corp_dl_agent.ml.synthetic import generate_clip_dataset
from corp_dl_agent.ml.taskspec import load_taskspec, taskspec_from_dict

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "ml"
CODE_HASH = "c" * 64


def small_spec(**overrides: Any) -> Any:
    d: dict[str, Any] = {
        "task_id": "s",
        "task_type": "regression",
        "data_path": "x.csv",
        "target": "y",
        "numeric_features": ["x1"],
        "id_column": "id",
        "group_column": "grp",
        "metric": "mae",
        "direction": "min",
        "data_origin": "synthetic",
    }
    d.update(overrides)
    return taskspec_from_dict(d)


def small_df(n_groups: int, rows_per_group: int = 3) -> Any:
    import pandas as pd

    rows = []
    for g in range(n_groups):
        for r in range(rows_per_group):
            rows.append({"id": f"{g:02d}{r:02d}", "grp": f"g{g}", "x1": float(g + r), "y": float(g * 2 + r)})
    return pd.DataFrame(rows)


def test_fixture_group_split_no_overlap_and_row_fractions() -> None:
    spec = load_taskspec(FIXTURES / "task_regression.yaml")
    df, rep = load_dataset(spec, roots=[FIXTURES])
    man = group_split(df, spec, code_hash=CODE_HASH, data_hash=rep.sha256)
    assert man.policy == "group" and man.overlap_checked
    assert man.n_groups == {"train": 28, "val": 6, "test": 6}
    assert sum(man.n_rows.values()) == 600
    assert set(man.groups["train"]) & set(man.groups["val"]) == set()
    assert set(man.groups["train"]) & set(man.groups["test"]) == set()
    assert set(man.groups["val"]) & set(man.groups["test"]) == set()
    assert (
        set(man.ids["train"]) & set(man.ids["val"]) == set()
        and set(man.ids["val"]) & set(man.ids["test"]) == set()
    )
    assert abs(sum(man.row_fractions.values()) - 1.0) < 1e-9
    # 요청 비율은 그룹 비율이고 실제 행 비율은 다를 수 있다 (보고값은 실제 행 수 기준)
    for name in ("train", "val", "test"):
        assert man.row_fractions[name] == pytest.approx(man.n_rows[name] / 600)
    assert set(man.hashes) == {"data", "split", "taskspec", "code", "lock"}
    assert (
        man.hashes["data"] == rep.sha256
        and man.hashes["code"] == CODE_HASH
        and man.hashes["lock"] == "UNLOCKED"
    )
    groups = df.set_index(spec.id_column)[spec.group_column]
    for name in ("train", "val", "test"):
        assert set(groups.loc[man.ids[name]].tolist()) == set(man.groups[name])
    with_lock = group_split(df, spec, code_hash=CODE_HASH, lock_hash="lock123")
    assert with_lock.hashes["lock"] == "lock123"
    assert verify_manifest_hashes(man, data_hash=rep.sha256, taskspec=spec, code_hash=CODE_HASH) == []
    assert verify_manifest_hashes(man, data_hash="0" * 64, code_hash="d" * 64) == ["data", "code"]


def test_deterministic_by_seed() -> None:
    df = small_df(10)
    spec = small_spec(seed=3)
    a = group_split(df, spec, code_hash=CODE_HASH)
    b = group_split(df, spec, code_hash=CODE_HASH)
    assert a.ids == b.ids and a.hashes["split"] == b.hashes["split"]
    c = group_split(df, small_spec(seed=4), code_hash=CODE_HASH)
    assert c.ids != a.ids


def test_too_few_groups_fails_instead_of_row_split() -> None:
    df = small_df(2, rows_per_group=50)
    with pytest.raises(AgentError) as ei:
        group_split(df, small_spec(), code_hash=CODE_HASH)
    assert ei.value.code == "E_SPLIT_IMPOSSIBLE"
    with pytest.raises(AgentError):
        allocate_group_counts(2)
    assert allocate_group_counts(3) == (1, 1, 1)
    assert allocate_group_counts(40) == (28, 6, 6)
    man = group_split(small_df(3), small_spec(), code_hash=CODE_HASH)
    assert man.n_groups == {"train": 1, "val": 1, "test": 1}


def test_time_policy_prevents_future_leakage() -> None:
    import pandas as pd

    rows = []
    for g in range(8):
        for r in range(4):
            rows.append(
                {
                    "id": f"{g:02d}{r:02d}",
                    "grp": f"g{g}",
                    "x1": float(r),
                    "y": float(g),
                    "t": f"2024-{(g % 12) + 1:02d}-{r + 1:02d}T00:00:00",
                }
            )
    df = pd.DataFrame(rows)
    spec = small_spec(split_policy="time", time_column="t")
    man = group_split(df, spec, code_hash=CODE_HASH)
    assert man.policy == "time" and man.time_column == "t"
    times = pd.to_datetime(df["t"])
    ids = df["id"]
    train_max = times[ids.isin(man.ids["train"])].max()
    val_max = times[ids.isin(man.ids["val"])].max()
    test_max = times[ids.isin(man.ids["test"])].max()
    assert train_max <= val_max <= test_max
    group_max = times.groupby(df["grp"]).max()
    assert train_max <= min(group_max[g] for g in man.groups["val"])
    assert val_max <= min(group_max[g] for g in man.groups["test"])
    assert man.time_boundaries["train_time_max"] <= man.time_boundaries["val_time_max"]
    assert "rule" in man.time_boundaries
    bad = df.copy()
    bad.loc[0, "t"] = "not a date"
    with pytest.raises(AgentError) as ei:
        group_split(bad, spec, code_hash=CODE_HASH)
    assert ei.value.code == "E_INPUT_INVALID"


def test_synthetic_time_split_on_classification_fixture() -> None:
    spec = load_taskspec(FIXTURES / "task_classification.yaml")
    df, rep = load_dataset(spec, roots=[FIXTURES])
    man = group_split(df, spec, code_hash=compute_code_hash(), data_hash=rep.sha256)
    assert man.policy == "time"
    assert (
        man.time_boundaries["train_time_max"]
        <= man.time_boundaries["val_time_max"]
        <= man.time_boundaries["test_time_max"]
    )
    tr, va, te = apply_split(df, man)
    assert tr[spec.time_column].max() <= va[spec.time_column].max() <= te[spec.time_column].max()
    assert set(tr[spec.group_column]) & set(te[spec.group_column]) == set()


def test_save_load_apply_roundtrip(tmp_path: Path) -> None:
    df = small_df(6)
    spec = small_spec()
    man = group_split(df, spec, code_hash=CODE_HASH)
    path = save_split(man, tmp_path / "한글" / "split_manifest.json")
    loaded = load_split(path)
    assert loaded == man
    tr, va, te = apply_split(df, loaded)
    assert len(tr) + len(va) + len(te) == len(df)
    assert set(tr["grp"]) & set(va["grp"]) == set() and set(tr["grp"]) & set(te["grp"]) == set()
    assert sorted(tr["id"].tolist()) == sorted(man.ids["train"])
    changed = df.drop(index=[0]).copy()
    with pytest.raises(AgentError) as ei:
        apply_split(changed, loaded)
    assert ei.value.code == "E_INPUT_INVALID" and ei.value.details["n_missing"] == 1
    extra = df.copy()
    extra.loc[len(extra)] = {"id": "zz", "grp": "g0", "x1": 0.0, "y": 0.0}
    with pytest.raises(AgentError) as ei2:
        apply_split(extra, loaded)
    assert ei2.value.details["n_extra"] == 1


def test_manifest_schema_strict(tmp_path: Path) -> None:
    man = group_split(small_df(4), small_spec(), code_hash=CODE_HASH)
    data = man.model_dump(mode="json")
    data["unexpected"] = 1
    from corp_dl_agent.common import atomic_write_json

    p = tmp_path / "m.json"
    atomic_write_json(p, data)
    with pytest.raises(AgentError) as ei:
        load_split(p)
    assert ei.value.code == "E_SCHEMA_INVALID"
    data.pop("unexpected")
    data["hashes"].pop("lock")
    with pytest.raises(ValidationError):
        SplitManifest.model_validate(data)
    with pytest.raises(AgentError):
        load_split(tmp_path / "none.json")


def test_duplicate_ids_or_empty_group_rejected() -> None:
    df = small_df(4)
    df.loc[1, "id"] = df.loc[0, "id"]
    with pytest.raises(AgentError) as ei:
        group_split(df, small_spec(), code_hash=CODE_HASH)
    assert ei.value.code == "E_INPUT_INVALID"
    df2 = small_df(4)
    df2.loc[2, "grp"] = ""
    with pytest.raises(AgentError):
        group_split(df2, small_spec(), code_hash=CODE_HASH)
    df3 = generate_clip_dataset(30, 5, 1, "regression")
    spec3 = small_spec(
        id_column="specimen_id",
        group_column="group_id",
        numeric_features=["thickness_mm"],
        target="insertion_force_n",
    )
    man3 = group_split(df3, spec3, code_hash=CODE_HASH)
    assert man3.n_groups == {"train": 3, "val": 1, "test": 1}
