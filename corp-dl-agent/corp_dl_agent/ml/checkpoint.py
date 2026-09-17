"""checkpoint 저장/복원 (epoch 완료 시점).

- 한 checkpoint = `<name>.pt` (텐서 + 메타 JSON 문자열, 단일 파일이라 원자적) + `<name>.json` (사람이 읽는 메타 사본 + pt sha256).
  `latest.pt` 와 `best.pt` 를 분리하고 `meta.json` 이 둘의 요약을 담는다.
- 내용: model/optimizer/scheduler/scaler state_dict, RNG(python/numpy/torch CPU/torch CUDA/DataLoader generator),
  next_epoch, best_validation, best_epoch, config_hash(fingerprint), metrics, extra(patience 카운터·history 등).
- 저장: 같은 폴더의 temp 파일에 torch.save → flush → fsync → temp 를 weights_only=True 로 재로드하여 검증 → os.replace.
- 로드: sidecar 의 sha256 검증 → torch.load(weights_only=True) (텐서/기본형만 허용) → 메타는 JSON 으로 검증.
  손상/형식 불일치 → E_CHECKPOINT_CORRUPT, config_hash 불일치 → E_FINGERPRINT_CHANGED.
- weights_only=True 는 임의 객체 역직렬화를 막지만 이것만으로 완전한 안전을 주장하지 않는다 (신뢰된 자기 생성 파일만 읽는다).
"""

from __future__ import annotations

import json
import os
import random
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import Field, ValidationError

from corp_dl_agent.common import (
    StrictModel,
    atomic_write_json,
    now_iso,
    read_json,
    sha256_file,
    validate_strict,
)
from corp_dl_agent.errors import AgentError, blocked_dependency
from corp_dl_agent.ml.mlp import torch_module
from corp_dl_agent.version import SCHEMA_VERSION, __version__

CHECKPOINT_FORMAT = "corp-dl-agent/checkpoint/1"
LATEST_NAME = "latest.pt"
BEST_NAME = "best.pt"
INDEX_NAME = "meta.json"
_TENSOR_KEYS = (
    "format",
    "meta_json",
    "model",
    "optimizer",
    "scheduler",
    "scaler",
    "rng_torch_cpu",
    "rng_torch_cuda",
    "rng_dataloader",
)


def _np() -> Any:
    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover
        raise blocked_dependency("numpy", "checkpoint") from exc
    return np


class CheckpointMeta(StrictModel):
    format: str = CHECKPOINT_FORMAT
    schema_version: str = SCHEMA_VERSION
    app_version: str = __version__
    torch_version: str = ""
    created_at: str = ""
    config_hash: str
    next_epoch: int = Field(ge=0)
    best_validation: float | None = None
    best_epoch: int | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    rng_python: list[Any] | None = None
    rng_numpy: dict[str, Any] | None = None
    has_optimizer: bool = False
    has_scheduler: bool = False
    has_scaler: bool = False
    has_cuda_rng: bool = False
    has_dataloader_rng: bool = False
    extra: dict[str, Any] = Field(default_factory=dict)


@dataclass
class LoadedCheckpoint:
    path: Path
    meta: CheckpointMeta
    model_state: dict[str, Any]
    optimizer_state: dict[str, Any] | None
    scheduler_state: dict[str, Any] | None
    scaler_state: dict[str, Any] | None
    rng: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------- RNG
def capture_rng(dataloader_generator: Any | None = None) -> dict[str, Any]:
    """python/numpy/torch(CPU, CUDA)/DataLoader generator 상태를 모은다. numpy/python 상태는 JSON 가능한 형태로 변환한다."""
    torch = torch_module("checkpoint")
    np = _np()
    py_state = random.getstate()
    np_state = np.random.get_state()
    out: dict[str, Any] = {
        "python": [int(py_state[0]), [int(x) for x in py_state[1]], py_state[2]],
        "numpy": {
            "name": str(np_state[0]),
            "key": [int(x) for x in np_state[1].tolist()],
            "pos": int(np_state[2]),
            "has_gauss": int(np_state[3]),
            "cached_gaussian": float(np_state[4]),
        },
        "torch_cpu": torch.get_rng_state(),
        "torch_cuda": list(torch.cuda.get_rng_state_all()) if torch.cuda.is_available() else [],
        "dataloader": dataloader_generator.get_state() if dataloader_generator is not None else None,
    }
    return out


def restore_rng(rng: dict[str, Any], dataloader_generator: Any | None = None) -> list[str]:
    """capture_rng 결과를 복원한다. 복원한 항목 이름 목록을 돌려준다 (CUDA 상태는 GPU 가 있을 때만)."""
    torch = torch_module("checkpoint")
    np = _np()
    restored: list[str] = []
    py = rng.get("python")
    if isinstance(py, list) and len(py) == 3:
        random.setstate((int(py[0]), tuple(int(x) for x in py[1]), py[2]))
        restored.append("python")
    nps = rng.get("numpy")
    if isinstance(nps, dict) and "key" in nps:
        np.random.set_state(
            (
                str(nps.get("name", "MT19937")),
                np.asarray(nps["key"], dtype="uint32"),
                int(nps.get("pos", 624)),
                int(nps.get("has_gauss", 0)),
                float(nps.get("cached_gaussian", 0.0)),
            )
        )
        restored.append("numpy")
    cpu = rng.get("torch_cpu")
    if cpu is not None:
        torch.set_rng_state(cpu.cpu() if hasattr(cpu, "cpu") else cpu)
        restored.append("torch_cpu")
    cuda_states = rng.get("torch_cuda") or []
    if cuda_states and torch.cuda.is_available():
        try:
            torch.cuda.set_rng_state_all([s.cpu() for s in cuda_states])
            restored.append("torch_cuda")
        except (RuntimeError, ValueError):
            pass
    dl = rng.get("dataloader")
    if dl is not None and dataloader_generator is not None:
        dataloader_generator.set_state(dl.cpu() if hasattr(dl, "cpu") else dl)
        restored.append("dataloader")
    return restored


# ---------------------------------------------------------------------------- save
def _sidecar_path(pt_path: Path) -> Path:
    return pt_path.with_suffix(".json")


def _corrupt(path: Path, why: str, **details: Any) -> AgentError:
    return AgentError(
        "E_CHECKPOINT_CORRUPT",
        f"checkpoint 검증 실패 ({path.name}): {why}",
        details={"path": str(path), **details},
    )


def _torch_load(path: Path, torch: Any) -> Any:
    try:
        return torch.load(path, map_location="cpu", weights_only=True)
    except AgentError:
        raise
    except Exception as exc:  # noqa: BLE001 - torch.load 는 다양한 예외(EOFError/RuntimeError/UnpicklingError/BadZipFile) 를 낸다
        raise _corrupt(path, f"{type(exc).__name__}: {str(exc)[:200]}") from exc


def _validate_payload(
    path: Path, payload: Any, expected_model_keys: list[str] | None = None
) -> CheckpointMeta:
    if not isinstance(payload, dict) or payload.get("format") != CHECKPOINT_FORMAT:
        raise _corrupt(path, "형식 표시(format) 가 이 프로그램의 checkpoint 가 아닙니다")
    missing = [k for k in _TENSOR_KEYS if k not in payload]
    if missing:
        raise _corrupt(path, f"필수 항목이 없습니다: {missing}")
    if not isinstance(payload.get("model"), dict) or not payload["model"]:
        raise _corrupt(path, "model state_dict 가 비어 있습니다")
    try:
        meta_obj = json.loads(str(payload["meta_json"]))
        meta: CheckpointMeta = validate_strict(CheckpointMeta, meta_obj)
    except (ValueError, ValidationError) as exc:
        raise _corrupt(path, f"메타 JSON 검증 실패: {str(exc)[:200]}") from exc
    if expected_model_keys is not None and sorted(payload["model"].keys()) != sorted(expected_model_keys):
        raise _corrupt(path, "재로드한 model state_dict 키가 저장한 것과 다릅니다")
    return meta


def save_checkpoint(
    path: str | os.PathLike[str],
    *,
    model: Any,
    optimizer: Any | None,
    scheduler: Any | None,
    scaler: Any | None,
    rng_state: dict[str, Any],
    next_epoch: int,
    best_validation: float | None,
    config_hash: str,
    metrics: dict[str, Any] | None = None,
    best_epoch: int | None = None,
    extra: dict[str, Any] | None = None,
) -> CheckpointMeta:
    """temp → flush/fsync → 재로드 검증 → os.replace. sidecar json 과 폴더 index(meta.json) 도 갱신한다."""
    torch = torch_module("checkpoint")
    pt_path = Path(path)
    pt_path.parent.mkdir(parents=True, exist_ok=True)
    meta = CheckpointMeta(
        torch_version=str(torch.__version__),
        created_at=now_iso(),
        config_hash=config_hash,
        next_epoch=int(next_epoch),
        best_validation=None if best_validation is None else float(best_validation),
        best_epoch=best_epoch,
        metrics=dict(metrics or {}),
        rng_python=rng_state.get("python"),
        rng_numpy=rng_state.get("numpy"),
        has_optimizer=optimizer is not None,
        has_scheduler=scheduler is not None,
        has_scaler=scaler is not None,
        has_cuda_rng=bool(rng_state.get("torch_cuda")),
        has_dataloader_rng=rng_state.get("dataloader") is not None,
        extra=dict(extra or {}),
    )
    model_state = {k: v.detach().cpu() for k, v in model.state_dict().items()}
    payload: dict[str, Any] = {
        "format": CHECKPOINT_FORMAT,
        "meta_json": json.dumps(meta.model_dump(mode="json"), ensure_ascii=False, sort_keys=True),
        "model": model_state,
        "optimizer": optimizer.state_dict() if optimizer is not None else None,
        "scheduler": scheduler.state_dict() if scheduler is not None else None,
        "scaler": scaler.state_dict() if scaler is not None else None,
        "rng_torch_cpu": rng_state.get("torch_cpu"),
        "rng_torch_cuda": list(rng_state.get("torch_cuda") or []),
        "rng_dataloader": rng_state.get("dataloader"),
    }
    fd, tmp_name = tempfile.mkstemp(prefix=f".{pt_path.name}.", suffix=".tmp", dir=str(pt_path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as f:
            torch.save(payload, f)
            f.flush()
            os.fsync(f.fileno())
        # 재로드 검증 (weights_only) — 손상된 파일을 latest/best 로 승격하지 않는다
        back = _torch_load(tmp, torch)
        _validate_payload(tmp, back, expected_model_keys=list(model_state.keys()))
        del back
        os.replace(tmp, pt_path)
    except BaseException:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise
    sidecar = meta.model_dump(mode="json")
    sidecar["pt_file"] = pt_path.name
    sidecar["pt_sha256"] = sha256_file(pt_path)
    sidecar["pt_size_bytes"] = pt_path.stat().st_size
    atomic_write_json(_sidecar_path(pt_path), sidecar)
    _update_index(pt_path.parent, pt_path.name, sidecar)
    return meta


def _update_index(directory: Path, name: str, sidecar: dict[str, Any]) -> None:
    index_path = directory / INDEX_NAME
    index: dict[str, Any] = {}
    if index_path.is_file():
        try:
            loaded = read_json(index_path)
            if isinstance(loaded, dict):
                index = loaded
        except (OSError, ValueError):
            index = {}
    index.setdefault("format", CHECKPOINT_FORMAT)
    files = index.setdefault("files", {})
    files[name] = {
        "sha256": sidecar["pt_sha256"],
        "next_epoch": sidecar["next_epoch"],
        "best_validation": sidecar["best_validation"],
        "best_epoch": sidecar["best_epoch"],
        "config_hash": sidecar["config_hash"],
        "created_at": sidecar["created_at"],
    }
    index["updated_at"] = now_iso()
    atomic_write_json(index_path, index)


# ---------------------------------------------------------------------------- load
def load_checkpoint(path: str | os.PathLike[str], expected_config_hash: str | None) -> LoadedCheckpoint:
    """sidecar sha256 → weights_only 로드 → 메타 검증 → config_hash 비교."""
    torch = torch_module("checkpoint")
    pt_path = Path(path)
    if not pt_path.is_file():
        raise _corrupt(pt_path, "파일이 없습니다")
    side = _sidecar_path(pt_path)
    if side.is_file():
        try:
            side_meta = read_json(side)
        except (OSError, ValueError) as exc:
            raise _corrupt(pt_path, "sidecar json 을 읽을 수 없습니다") from exc
        expected_sha = side_meta.get("pt_sha256") if isinstance(side_meta, dict) else None
        if not expected_sha or sha256_file(pt_path) != expected_sha:
            raise _corrupt(pt_path, "sha256 이 sidecar 기록과 다릅니다")
    payload = _torch_load(pt_path, torch)
    meta = _validate_payload(pt_path, payload)
    if expected_config_hash is not None and meta.config_hash != expected_config_hash:
        raise AgentError(
            "E_FINGERPRINT_CHANGED",
            f"checkpoint 의 config hash 가 현재 실험과 다릅니다 ({pt_path.name})",
            details={
                "path": str(pt_path),
                "stored": meta.config_hash[:16],
                "current": expected_config_hash[:16],
            },
        )
    rng = {
        "python": meta.rng_python,
        "numpy": meta.rng_numpy,
        "torch_cpu": payload.get("rng_torch_cpu"),
        "torch_cuda": payload.get("rng_torch_cuda") or [],
        "dataloader": payload.get("rng_dataloader"),
    }
    return LoadedCheckpoint(
        path=pt_path,
        meta=meta,
        model_state=dict(payload["model"]),
        optimizer_state=payload.get("optimizer"),
        scheduler_state=payload.get("scheduler"),
        scaler_state=payload.get("scaler"),
        rng=rng,
    )


def read_index(directory: str | os.PathLike[str]) -> dict[str, Any]:
    """checkpoints/meta.json (없으면 빈 dict). torch 없이 읽을 수 있다."""
    p = Path(directory) / INDEX_NAME
    if not p.is_file():
        return {}
    try:
        data = read_json(p)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}
