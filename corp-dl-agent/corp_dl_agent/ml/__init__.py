"""ml-core: 데이터 계약(TaskSpec)·데이터 검증·고정 그룹 분할·train-only 전처리·기준 모델·평가·후보 registry·합성 데이터.

이 패키지 최상위는 표준 라이브러리만 사용한다. numpy/pandas/scikit-learn 은 각 모듈의 함수 안에서 lazy import 한다.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

UNLOCKED = "UNLOCKED"


def package_root() -> Path:
    """corp_dl_agent 패키지 폴더."""
    return Path(__file__).resolve().parents[1]


def code_hash_manifest(root: Path | None = None) -> dict[str, str]:
    """패키지 안의 모든 .py 파일 (posix 상대경로 -> sha256). __pycache__ 제외, 경로 정렬."""
    base = (root or package_root()).resolve()
    out: dict[str, str] = {}
    for p in sorted(base.rglob("*.py"), key=lambda x: x.relative_to(base).as_posix()):
        if "__pycache__" in p.parts:
            continue
        h = hashlib.sha256()
        with open(p, "rb") as f:
            h.update(f.read())
        out[p.relative_to(base).as_posix()] = h.hexdigest()
    return out


def compute_code_hash(root: Path | None = None) -> str:
    """corp_dl_agent 패키지 소스(.py) 전체의 집계 sha256.

    각 파일의 (posix 상대경로, sha256) 을 정렬된 순서로 이어 붙여 다시 sha256 한다.
    OS 에 무관하게 같은 소스면 같은 값이 나온다 (줄바꿈은 파일 바이트 그대로 반영).
    """
    lines = [f"{rel}\n{digest}\n" for rel, digest in code_hash_manifest(root).items()]
    return hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()


def normalize_lock_hash(lock_hash: str | None) -> str:
    """lock hash 가 없으면 'UNLOCKED' 로 명시한다 (값을 만들어내지 않는다)."""
    if lock_hash is None or not str(lock_hash).strip():
        return UNLOCKED
    return str(lock_hash).strip()


__all__ = ["UNLOCKED", "code_hash_manifest", "compute_code_hash", "normalize_lock_hash", "package_root"]
