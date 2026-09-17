"""MLP(shape/손실) 와 checkpoint(원자적 저장·재로드 검증·RNG 복원·hash 불일치·손상) 시험."""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from corp_dl_agent.errors import AgentError
from corp_dl_agent.ml.checkpoint import (
    BEST_NAME,
    INDEX_NAME,
    LATEST_NAME,
    capture_rng,
    load_checkpoint,
    read_index,
    restore_rng,
    save_checkpoint,
)
from corp_dl_agent.ml.mlp import (
    MlpArch,
    build_mlp,
    check_output_target,
    count_parameters,
    make_loss,
    outputs_to_predictions,
)

pytestmark = pytest.mark.torch


def _arch(task: str = "regression", in_dim: int = 5) -> MlpArch:
    return MlpArch(task_type=task, in_dim=in_dim, hidden_dims=[64, 32], dropout=0.1)


def test_mlp_output_shape_and_regression_loss() -> None:
    import torch

    torch.manual_seed(0)
    model = build_mlp(_arch())
    x = torch.randn(8, 5)
    out = model(x)
    assert tuple(out.shape) == (8, 1) and out.dtype == torch.float32
    y = torch.randn(8, 1)
    check_output_target(out, y, "regression")
    loss = make_loss("regression")(out, y)
    assert loss.dim() == 0 and torch.isfinite(loss)
    with pytest.raises(AgentError) as ei:
        check_output_target(out, torch.randn(8), "regression")  # (N,) target 은 shape 불일치
    assert ei.value.code == "E_INTERNAL"
    with pytest.raises(AgentError) as ei2:
        model(torch.randn(8, 4))
    assert ei2.value.code == "E_INPUT_INVALID"
    assert count_parameters(model) == 5 * 64 + 64 + 64 * 32 + 32 + 32 + 1


def test_mlp_classification_logits_and_bce() -> None:
    import torch

    torch.manual_seed(0)
    model = build_mlp(_arch("binary_classification"))
    x = torch.randn(6, 5)
    logits = model(x)
    y = torch.tensor([[0.0], [1.0], [1.0], [0.0], [1.0], [0.0]])
    check_output_target(logits, y, "binary_classification")
    loss = make_loss("binary_classification")(logits, y)
    assert isinstance(make_loss("binary_classification"), torch.nn.BCEWithLogitsLoss)
    assert torch.isfinite(loss)
    prob = outputs_to_predictions(logits, "binary_classification")
    assert tuple(prob.shape) == (6,) and bool((prob >= 0).all()) and bool((prob <= 1).all())
    with pytest.raises(AgentError):
        check_output_target(logits, torch.full((6, 1), 2.0), "binary_classification")


def test_arch_validation() -> None:
    with pytest.raises(ValueError):
        MlpArch(task_type="regression", in_dim=3, hidden_dims=[], dropout=0.0)
    with pytest.raises(ValueError):
        MlpArch(task_type="regression", in_dim=3, hidden_dims=[64, 32], dropout=0.0, feature_names=["a"])
    with pytest.raises(ValueError):
        MlpArch(format="other", task_type="regression", in_dim=3, hidden_dims=[8], dropout=0.0)


def _model_bundle() -> tuple:
    import torch

    torch.manual_seed(1)
    model = build_mlp(_arch())
    opt = torch.optim.AdamW(model.parameters(), lr=1e-3, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=5)
    loss = model(torch.randn(4, 5)).sum()
    loss.backward()
    opt.step()
    sched.step()
    return model, opt, sched


def test_checkpoint_roundtrip_restores_rng_and_states(tmp_path: Path) -> None:
    import numpy as np
    import torch

    model, opt, sched = _model_bundle()
    gen = torch.Generator()
    gen.manual_seed(3)
    random.seed(5)
    np.random.seed(6)
    torch.manual_seed(7)
    rng = capture_rng(gen)
    path = tmp_path / "체크포인트" / LATEST_NAME
    meta = save_checkpoint(
        path,
        model=model,
        optimizer=opt,
        scheduler=sched,
        scaler=None,
        rng_state=rng,
        next_epoch=3,
        best_validation=1.25,
        best_epoch=2,
        config_hash="cfg-1",
        metrics={"mae": 1.25},
        extra={"bad_epochs": 1},
    )
    assert meta.next_epoch == 3 and meta.has_optimizer and meta.has_scheduler and not meta.has_scaler
    expected = (random.random(), float(np.random.rand()), torch.rand(1).item(), torch.rand(1, generator=gen).item())
    # 다른 값을 뽑아 상태를 흐트러뜨린 뒤 복원
    random.random()
    np.random.rand()
    torch.rand(3)
    torch.rand(2, generator=gen)
    ck = load_checkpoint(path, "cfg-1")
    assert ck.meta.best_validation == 1.25 and ck.meta.extra == {"bad_epochs": 1} and ck.meta.metrics == {"mae": 1.25}
    restored = restore_rng(ck.rng, gen)
    assert {"python", "numpy", "torch_cpu", "dataloader"} <= set(restored)
    got = (random.random(), float(np.random.rand()), torch.rand(1).item(), torch.rand(1, generator=gen).item())
    assert got == expected
    model2 = build_mlp(_arch())
    model2.load_state_dict(ck.model_state, strict=True)
    for a, b in zip(model.state_dict().values(), model2.state_dict().values(), strict=True):
        assert torch.equal(a, b)
    opt2 = torch.optim.AdamW(model2.parameters(), lr=1e-3)
    assert ck.optimizer_state is not None
    opt2.load_state_dict(ck.optimizer_state)
    assert opt2.state_dict()["param_groups"][0]["lr"] == opt.state_dict()["param_groups"][0]["lr"]
    assert ck.scheduler_state is not None and ck.scheduler_state["last_epoch"] == 1
    side = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    assert side["pt_file"] == LATEST_NAME and len(side["pt_sha256"]) == 64
    index = read_index(path.parent)
    assert LATEST_NAME in index["files"] and index["files"][LATEST_NAME]["next_epoch"] == 3
    assert not list(path.parent.glob("*.tmp"))


def test_checkpoint_latest_and_best_are_separate(tmp_path: Path) -> None:
    model, opt, sched = _model_bundle()
    rng = capture_rng()
    save_checkpoint(tmp_path / LATEST_NAME, model=model, optimizer=opt, scheduler=sched, scaler=None, rng_state=rng, next_epoch=4, best_validation=2.0, config_hash="c", metrics={})
    save_checkpoint(tmp_path / BEST_NAME, model=model, optimizer=opt, scheduler=sched, scaler=None, rng_state=rng, next_epoch=2, best_validation=2.0, config_hash="c", metrics={})
    index = read_index(tmp_path)
    assert set(index["files"]) == {LATEST_NAME, BEST_NAME}
    assert load_checkpoint(tmp_path / LATEST_NAME, "c").meta.next_epoch == 4
    assert load_checkpoint(tmp_path / BEST_NAME, "c").meta.next_epoch == 2
    assert (tmp_path / INDEX_NAME).is_file()


def test_checkpoint_config_hash_mismatch_rejected(tmp_path: Path) -> None:
    model, opt, sched = _model_bundle()
    path = tmp_path / LATEST_NAME
    save_checkpoint(path, model=model, optimizer=opt, scheduler=sched, scaler=None, rng_state=capture_rng(), next_epoch=1, best_validation=None, config_hash="hash-A", metrics={})
    with pytest.raises(AgentError) as ei:
        load_checkpoint(path, "hash-B")
    assert ei.value.code == "E_FINGERPRINT_CHANGED"
    assert load_checkpoint(path, None).meta.config_hash == "hash-A"


def test_checkpoint_corruption_detected(tmp_path: Path) -> None:
    import torch

    model, opt, sched = _model_bundle()
    path = tmp_path / LATEST_NAME
    save_checkpoint(path, model=model, optimizer=opt, scheduler=sched, scaler=None, rng_state=capture_rng(), next_epoch=1, best_validation=None, config_hash="c", metrics={})
    good = path.read_bytes()
    path.write_bytes(good[: len(good) // 2])  # 잘린 파일
    with pytest.raises(AgentError) as ei:
        load_checkpoint(path, "c")
    assert ei.value.code == "E_CHECKPOINT_CORRUPT"
    path.write_bytes(good)
    side = path.with_suffix(".json")
    meta = json.loads(side.read_text(encoding="utf-8"))
    meta["pt_sha256"] = "0" * 64
    side.write_text(json.dumps(meta), encoding="utf-8")
    with pytest.raises(AgentError) as ei2:
        load_checkpoint(path, "c")
    assert ei2.value.code == "E_CHECKPOINT_CORRUPT" and "sha256" in ei2.value.message
    side.unlink()
    assert load_checkpoint(path, "c").meta.next_epoch == 1  # sidecar 가 없으면 내용 검증만으로 로드
    foreign = tmp_path / "foreign.pt"
    torch.save({"model": model.state_dict()}, foreign)  # format 표시 없는 외부 파일
    with pytest.raises(AgentError) as ei3:
        load_checkpoint(foreign, "c")
    assert ei3.value.code == "E_CHECKPOINT_CORRUPT"
    with pytest.raises(AgentError) as ei4:
        load_checkpoint(tmp_path / "missing.pt", "c")
    assert ei4.value.code == "E_CHECKPOINT_CORRUPT"
