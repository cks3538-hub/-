"""TaskSpec strict schema 시험: 추가 필드/dtype/범위/명령 문자열 거부, metric·direction 일관성, hash, code hash."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from corp_dl_agent.errors import AgentError
from corp_dl_agent.ml import compute_code_hash, normalize_lock_hash
from corp_dl_agent.ml.evaluation import acceptance_status
from corp_dl_agent.ml.taskspec import (
    NEEDS_ACCEPTANCE_CRITERIA,
    Acceptance,
    TaskSpec,
    load_taskspec,
    taskspec_from_dict,
    taskspec_hash,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "ml"


def base_spec(**overrides: Any) -> dict[str, Any]:
    d: dict[str, Any] = {
        "task_id": "t1",
        "task_type": "regression",
        "data_path": "data.csv",
        "target": "y",
        "numeric_features": ["x1", "x2"],
        "categorical_features": ["c1"],
        "units": {"x1": "mm", "y": "N"},
        "id_column": "id",
        "group_column": "grp",
        "seed": 7,
        "metric": "mae",
        "direction": "min",
        "data_origin": "synthetic",
    }
    d.update(overrides)
    return d


def test_fixture_specs_load_and_resolve_data_path() -> None:
    for name in ("task_regression.yaml", "task_classification.yaml"):
        spec = load_taskspec(FIXTURES / name)
        assert Path(spec.data_path).is_absolute() and Path(spec.data_path).is_file()
        assert spec.data_origin == "synthetic"
        assert spec.acceptance is None and spec.needs_acceptance_criteria()
        assert spec.id_column not in spec.features and spec.group_column not in spec.features
        assert "post_retention_force_n" in spec.excluded_columns
        assert len(taskspec_hash(spec)) == 64


def test_taskspec_hash_independent_of_location(tmp_path: Path) -> None:
    src = FIXTURES / "task_regression.yaml"
    dst_dir = tmp_path / "다른 위치"
    dst_dir.mkdir()
    shutil.copy(src, dst_dir / "task_regression.yaml")
    shutil.copy(FIXTURES / "clip_regression.csv", dst_dir / "clip_regression.csv")
    a = load_taskspec(src)
    b = load_taskspec(dst_dir / "task_regression.yaml")
    assert a.data_path != b.data_path
    assert taskspec_hash(a) == taskspec_hash(b)
    c = b.model_copy(update={"seed": 99})
    assert taskspec_hash(c) != taskspec_hash(b)


def test_extra_field_rejected() -> None:
    with pytest.raises(AgentError) as ei:
        taskspec_from_dict(base_spec(extra_field=1))
    assert ei.value.code == "E_SCHEMA_INVALID"
    assert any("extra_field" in e for e in ei.value.details["errors"])


@pytest.mark.parametrize(
    "overrides",
    [
        {"seed": "42"},
        {"seed": 4.5},
        {"numeric_features": "x1"},
        {"units": {"x1": 3}},
        {"acceptance": {"metric": "mae", "threshold": "1", "direction": "min"}},
        {"resource_budget": {"mode": "demo", "max_epochs": "10"}},
    ],
)
def test_wrong_dtype_rejected(overrides: dict[str, Any]) -> None:
    with pytest.raises(AgentError) as ei:
        taskspec_from_dict(base_spec(**overrides))
    assert ei.value.code == "E_SCHEMA_INVALID"


@pytest.mark.parametrize(
    "overrides",
    [
        {"seed": -1},
        {"seed": 2**31},
        {"resource_budget": {"mode": "demo", "wall_time_seconds": 5}},
        {"resource_budget": {"mode": "custom", "wall_time_seconds": 100}},
        {"resource_budget": {"mode": "pilot", "max_candidates": 65}},
        {"acceptance": {"metric": "r2", "threshold": 1.5, "direction": "max"}},
        {"acceptance": {"metric": "mae", "threshold": -1.0, "direction": "min"}},
        {"task_id": "a" * 65},
        {"task_id": "bad id"},
    ],
)
def test_range_rejected(overrides: dict[str, Any]) -> None:
    with pytest.raises(AgentError) as ei:
        taskspec_from_dict(base_spec(**overrides))
    assert ei.value.code == "E_SCHEMA_INVALID"


@pytest.mark.parametrize(
    "overrides",
    [
        {"data_path": "data.csv; rm -rf /"},
        {"data_path": "$(curl http://x)/data.csv"},
        {"data_path": "https://example.com/data.csv"},
        {"data_path": "data.exe"},
        {"target": "y\nrm -rf /"},
        {"id_column": "id | cat /etc/passwd"},
        {"numeric_features": ["x1", "`whoami`"]},
        {"units": {"x1": "mm && echo"}},
        {"purpose": "설명 $(rm -rf /)"},
    ],
)
def test_command_strings_rejected(overrides: dict[str, Any]) -> None:
    with pytest.raises(AgentError) as ei:
        taskspec_from_dict(base_spec(**overrides))
    assert ei.value.code == "E_SCHEMA_INVALID"


@pytest.mark.parametrize(
    "overrides",
    [
        {"metric": "f1"},  # regression 에 분류 metric
        {"metric": "rmse", "direction": "max"},
        {"task_type": "binary_classification", "metric": "mae", "direction": "min"},
        {"task_type": "binary_classification", "metric": "roc_auc", "direction": "min"},
        {"acceptance": {"metric": "f1", "threshold": 0.8, "direction": "max"}},  # task 와 불일치
        {"acceptance": {"metric": "mae", "threshold": 1.0, "direction": "max"}},  # 방향 불일치
    ],
)
def test_metric_direction_consistency(overrides: dict[str, Any]) -> None:
    with pytest.raises(AgentError) as ei:
        taskspec_from_dict(base_spec(**overrides))
    assert ei.value.code == "E_SCHEMA_INVALID"


@pytest.mark.parametrize(
    "overrides",
    [
        {"numeric_features": ["x1", "y"]},
        {"categorical_features": ["id"]},
        {"numeric_features": ["grp"]},
        {"numeric_features": ["x1", "x1"]},
        {"numeric_features": ["x1"], "categorical_features": ["x1"]},
        {"excluded_columns": ["x1"]},
        {"excluded_columns": ["y"]},
        {"id_column": "grp"},
        {"numeric_features": [], "categorical_features": []},
        {"split_policy": "time"},
        {"split_policy": "time", "time_column": "x1"},
        {"units": {"c1": "mm"}},
    ],
)
def test_leakage_and_structure_rules(overrides: dict[str, Any]) -> None:
    with pytest.raises(AgentError) as ei:
        taskspec_from_dict(base_spec(**overrides))
    assert ei.value.code == "E_SCHEMA_INVALID"


def test_valid_variants() -> None:
    s = taskspec_from_dict(
        base_spec(
            split_policy="time",
            time_column="t",
            acceptance={"metric": "mae", "threshold": 2.5, "direction": "min"},
        )
    )
    assert s.time_column == "t" and s.acceptance is not None and s.acceptance.threshold == 2.5
    assert s.required_columns() == ["id", "grp", "y", "x1", "x2", "c1", "t"]
    assert s.target_unit() == "N"
    cls = taskspec_from_dict(
        base_spec(
            task_type="binary_classification", metric="average_precision", direction="max", units={"x1": "mm"}
        )
    )
    assert cls.task_type == "binary_classification"
    custom = taskspec_from_dict(
        base_spec(
            resource_budget={
                "mode": "custom",
                "wall_time_seconds": 60,
                "max_candidates": 2,
                "max_epochs": 5,
                "patience": 2,
            }
        )
    )
    assert custom.resource_budget.mode == "custom"


def test_load_json_and_yaml_errors(tmp_path: Path) -> None:
    jp = tmp_path / "spec.json"
    jp.write_text(json.dumps(base_spec()), encoding="utf-8")
    (tmp_path / "data.csv").write_text("id\n", encoding="utf-8")
    s = load_taskspec(jp)
    assert s.data_path == str((tmp_path / "data.csv").resolve())
    bad = tmp_path / "bad.yaml"
    bad.write_text("task_id: [unclosed", encoding="utf-8")
    with pytest.raises(AgentError) as ei:
        load_taskspec(bad)
    assert ei.value.code == "E_SCHEMA_INVALID"
    lst = tmp_path / "list.yaml"
    lst.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(AgentError) as ei2:
        load_taskspec(lst)
    assert ei2.value.code == "E_SCHEMA_INVALID"
    with pytest.raises(AgentError) as ei3:
        load_taskspec(tmp_path / "missing.yaml")
    assert ei3.value.code == "E_INPUT_INVALID"
    txt = tmp_path / "spec.txt"
    txt.write_text(yaml.safe_dump(base_spec()), encoding="utf-8")
    with pytest.raises(AgentError) as ei4:
        load_taskspec(txt)
    assert ei4.value.code == "E_INPUT_INVALID"


def test_yaml_command_injection_string_rejected(tmp_path: Path) -> None:
    d = base_spec(data_path="data.csv && del /q *")
    p = tmp_path / "spec.yaml"
    p.write_text(yaml.safe_dump(d, allow_unicode=True), encoding="utf-8")
    with pytest.raises(AgentError) as ei:
        load_taskspec(p)
    assert ei.value.code == "E_SCHEMA_INVALID"


def test_acceptance_helpers() -> None:
    assert acceptance_status(None, {"mae": 1.0}).state == NEEDS_ACCEPTANCE_CRITERIA
    acc = Acceptance(metric="mae", threshold=2.0, direction="min")
    assert acceptance_status(acc, {"mae": 1.5}).state == "PASS"
    assert acceptance_status(acc, {"mae": 2.5}).state == "FAIL"
    assert acceptance_status(acc, {"rmse": 2.5}).state == "METRIC_MISSING"
    assert acceptance_status(acc, {"mae": float("nan")}).state == "METRIC_MISSING"
    acc2 = Acceptance(metric="f1", threshold=0.8, direction="max")
    assert acceptance_status(acc2, {"f1": 0.8}).state == "PASS"
    assert acceptance_status(acc2, {"f1": 0.79}).state == "FAIL"
    with pytest.raises(ValidationError):
        Acceptance(metric="f1", threshold=0.8, direction="min")


def test_strict_model_rejects_direct_bad_types() -> None:
    with pytest.raises(ValidationError):
        TaskSpec(**base_spec(seed="7"))  # type: ignore[arg-type]


def test_compute_code_hash_and_lock(tmp_path: Path) -> None:
    h = compute_code_hash()
    assert len(h) == 64 and h == compute_code_hash()
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "a.py").write_text("x = 1\n", encoding="utf-8")
    (pkg / "__pycache__").mkdir()
    (pkg / "__pycache__" / "a.cpython-312.pyc").write_bytes(b"ignored")
    h1 = compute_code_hash(pkg)
    (pkg / "a.py").write_text("x = 2\n", encoding="utf-8")
    h2 = compute_code_hash(pkg)
    assert h1 != h2
    (pkg / "__pycache__" / "a.cpython-312.pyc").write_bytes(b"changed")
    assert compute_code_hash(pkg) == h2
    assert normalize_lock_hash(None) == "UNLOCKED"
    assert normalize_lock_hash("  ") == "UNLOCKED"
    assert normalize_lock_hash("abc") == "abc"
