"""타깃 프로파일 (TargetProfile) — 참조 타깃 win-x64-cp312-cpu, 호스트용 linux-x64-cp312-cpu, 호스트 감지.

profile_id 규칙: <os>-<arch>-cp<XY>-<cpu|gpu>  (os: win|linux|macos, arch: x64|arm64)
target_profile.json 은 상세(verified_environment 포함, 로컬 보관), target_profile.min.json 은 허용 필드만
(이름/호스트/사용자 경로/URL/파일 목록/인증값 제외).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from corp_dl_agent.common import StrictModel, atomic_write_json, read_json, validate_strict
from corp_dl_agent.errors import AgentError
from corp_dl_agent.packaging import verifier_core as core

PROFILE_ID_RE = core.PROFILE_ID_RE
DEFAULT_FEATURES = list(core.DEFAULT_FEATURES)


class TargetProfile(StrictModel):
    """설치 대상 환경. target_confirmed=False 이면 임시 참조값이다."""

    profile_id: str
    os: str
    os_version: str = ""
    architecture: str
    python_implementation: str = "CPython"
    python_version: str
    python_abi: str
    platform_tags: list[str]
    features: list[str] = Field(default_factory=lambda: list(DEFAULT_FEATURES))
    device: Literal["cpu", "gpu"] = "cpu"
    gpu: dict[str, str | None] | None = None
    target_confirmed: bool = False
    verified_environment: dict[str, Any] | None = None
    notes: list[str] = Field(default_factory=list)

    @field_validator("profile_id")
    @classmethod
    def _profile_id_format(cls, v: str) -> str:
        if not PROFILE_ID_RE.match(v):
            raise ValueError(f"profile_id 형식은 <win|linux|macos>-<x64|arm64>-cp<XY>-<cpu|gpu> 입니다: {v}")
        return v

    @field_validator("python_abi")
    @classmethod
    def _abi_format(cls, v: str) -> str:
        if not core.re.match(r"^[a-z]{2}\d{2,3}$", v):
            raise ValueError(f"python_abi 형식 오류 (예: cp312): {v}")
        return v

    @model_validator(mode="after")
    def _consistent(self) -> TargetProfile:
        expected = core.profile_id_for(self.os, self.architecture, self.python_abi, self.device)
        if expected != self.profile_id:
            raise ValueError(
                f"profile_id {self.profile_id} 가 os/architecture/python_abi/device 와 일치하지 않습니다 (기대 {expected})"
            )
        major_minor = ".".join(self.python_version.split(".")[:2])
        if self.python_abi[2:] != major_minor.replace(".", ""):
            raise ValueError(
                f"python_abi {self.python_abi} 와 python_version {self.python_version} 이 일치하지 않습니다"
            )
        if not self.platform_tags:
            raise ValueError("platform_tags 가 비어 있습니다")
        if self.device == "gpu" and self.gpu is None:
            raise ValueError("device=gpu 이면 gpu 정보(확인된 드라이버/torch 빌드)가 필요합니다")
        return self

    def to_core_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def min_dict(
        self, *, preflight_status: str = "NOT_RUN", feature_status: dict[str, str] | None = None
    ) -> dict[str, Any]:
        return core.min_profile(
            self.to_core_dict(), preflight_status=preflight_status, feature_status=feature_status
        )


WIN_X64_CP312_CPU = TargetProfile(
    profile_id="win-x64-cp312-cpu",
    os="Windows",
    os_version="10/11 x64 (미확정)",
    architecture="x64",
    python_implementation="CPython",
    python_version="3.12",
    python_abi="cp312",
    platform_tags=["win_amd64"],
    features=list(DEFAULT_FEATURES),
    device="cpu",
    gpu=None,
    target_confirmed=False,
    verified_environment=None,
    notes=[
        "사내 타깃이 주어지지 않아 Windows x64 / CPython 3.12 / CPU 를 임시 참조 프로파일로 사용한다 (target_confirmed=false).",
        "torch 는 PyPI Windows CPU 빌드. GPU 는 별도 프로파일(드라이버/torch 빌드 조합 확인 필요).",
        "cross-download 성공은 TARGET_BUNDLE_PREPARED 이며 타깃 실행 시험(TARGET_OFFLINE_TESTED)을 뜻하지 않는다.",
    ],
)

LINUX_X64_CP312_CPU = TargetProfile(
    profile_id="linux-x64-cp312-cpu",
    os="Linux",
    os_version="glibc>=2.28 x86_64 (개발 호스트 rehearsal 용)",
    architecture="x64",
    python_implementation="CPython",
    python_version="3.12",
    python_abi="cp312",
    platform_tags=["manylinux_2_28_x86_64", "linux_x86_64"],
    features=list(DEFAULT_FEATURES),
    device="cpu",
    gpu=None,
    target_confirmed=False,
    verified_environment=None,
    notes=[
        "개발 호스트(Linux x86_64) 에서 새 경로 설치 rehearsal 에 쓰는 프로파일. 사내 타깃이 아니다.",
        "Linux x64 의 PyPI torch wheel 은 CUDA 빌드(+nvidia 패키지)이며 CPU 에서도 동작하지만 크기가 크다.",
    ],
)

REFERENCE_PROFILES: dict[str, TargetProfile] = {
    WIN_X64_CP312_CPU.profile_id: WIN_X64_CP312_CPU,
    LINUX_X64_CP312_CPU.profile_id: LINUX_X64_CP312_CPU,
}


def get_profile(profile_id: str) -> TargetProfile:
    try:
        return REFERENCE_PROFILES[profile_id].model_copy(deep=True)
    except KeyError as exc:
        raise AgentError(
            "E_NOT_SUPPORTED",
            f"알 수 없는 타깃 프로파일: {profile_id}",
            hint="정의된 프로파일: "
            + ", ".join(sorted(REFERENCE_PROFILES))
            + " (새 타깃은 target.py 에 정의하고 wheelhouse 를 준비해야 합니다)",
        ) from exc


def profile_from_dict(data: dict[str, Any]) -> TargetProfile:
    allowed = set(TargetProfile.model_fields)
    filtered = {k: v for k, v in data.items() if k in allowed}
    return validate_strict(TargetProfile, filtered)


def detect_host() -> TargetProfile:
    """현 호스트를 TargetProfile 로 감지 (target_confirmed=False, verified_environment 에 감지 정보)."""
    return profile_from_dict(core.detect_host())


def load_target_profile(path: str | os.PathLike[str]) -> TargetProfile:
    data = read_json(path)
    if not isinstance(data, dict):
        raise AgentError("E_SCHEMA_INVALID", f"target profile 파일 형식 오류: {Path(path).name}")
    try:
        return profile_from_dict(data)
    except ValueError as exc:
        raise AgentError(
            "E_SCHEMA_INVALID",
            f"target profile 검증 실패: {Path(path).name}",
            details={"error": str(exc)[:500]},
        ) from exc


def write_target_profile(profile: TargetProfile, path: str | os.PathLike[str]) -> Path:
    p = Path(path)
    atomic_write_json(p, profile.model_dump(mode="json"))
    return p


def write_min_profile(
    profile: TargetProfile,
    path: str | os.PathLike[str],
    *,
    preflight_status: str = "NOT_RUN",
    feature_status: dict[str, str] | None = None,
) -> Path:
    """허용 필드만 담은 target_profile.min.json."""
    p = Path(path)
    atomic_write_json(p, profile.min_dict(preflight_status=preflight_status, feature_status=feature_status))
    return p
