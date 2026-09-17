"""앱 설정 strict schema.

- 알 수 없는 키·잘못된 dtype·범위를 거부한다 (extra=forbid).
- secret 은 값이 아니라 참조(env:NAME | file:<path>)만 허용한다.
- 이 프로파일들은 이 앱의 자체 설정이며 Claude Code 의 설정 키가 아니다.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal

from pydantic import Field, field_validator, model_validator

from corp_dl_agent.common import StrictModel

SECRET_REF_RE = re.compile(r"^(env|file):.+$")


class Profile(StrEnum):
    PERSONAL_DEV = "personal-dev"
    TRANSFER_TEST = "transfer-test"
    CORP_OFFLINE = "corp-offline"
    CORP_GATEWAY = "corp-gateway"

    @property
    def network_allowed(self) -> bool:
        return self is Profile.CORP_GATEWAY


class PathsConfig(StrictModel):
    """모든 경로는 절대 경로 또는 config 파일 기준 상대 경로. 한글/공백 허용."""

    install_root: str | None = None
    data_root: str = "workspace"
    company_root: str = "company"
    input_roots: list[str] = Field(default_factory=list)
    sources_roots: list[str] = Field(default_factory=list)
    output_root: str | None = None  # None -> <data_root>/outputs


class BudgetConfig(StrictModel):
    max_candidates: int = Field(2, ge=1, le=64)
    max_epochs: int = Field(10, ge=1, le=10000)
    patience: int = Field(3, ge=1, le=1000)
    wall_time_seconds: int = Field(300, ge=10, le=7 * 24 * 3600)


class MlConfig(StrictModel):
    device: Literal["cpu", "cuda"] = "cpu"
    num_workers: int = Field(0, ge=0, le=32)
    amp: bool = False
    demo: BudgetConfig = Field(
        default_factory=lambda: BudgetConfig(
            max_candidates=2, max_epochs=10, patience=3, wall_time_seconds=300
        )
    )
    pilot: BudgetConfig = Field(
        default_factory=lambda: BudgetConfig(
            max_candidates=6, max_epochs=100, patience=10, wall_time_seconds=3600
        )
    )
    oom_retries: int = Field(2, ge=0, le=2)
    nan_retries: int = Field(1, ge=0, le=1)
    allow_synthetic_models_in_production: bool = False


class GatewayConfig(StrictModel):
    """사내 LLM gateway. 값이 없으면 호출하지 않는다 (public provider fallback 없음)."""

    enabled: bool = False
    base_url: str | None = None
    api_format: Literal["openai_chat", "anthropic_messages", "gemini_custom"] = "openai_chat"
    model_id: str | None = None
    secret_ref: str | None = None
    ca_bundle: str | None = None
    approved_origins: list[str] = Field(default_factory=list)
    timeout_seconds: float = Field(60.0, gt=0, le=600)
    max_calls: int = Field(12, ge=1, le=1000)
    max_total_tokens: int = Field(30000, ge=1)
    max_response_tokens: int = Field(2000, ge=1)
    retry_transient: int = Field(2, ge=0, le=2)
    json_repair_attempts: int = Field(1, ge=0, le=1)
    reserve_tokens_per_call: int = Field(2000, ge=0, description="usage 미제공 시 보수적 예약")
    extra_headers: dict[str, str] = Field(default_factory=dict)

    @field_validator("secret_ref")
    @classmethod
    def _secret_ref_format(cls, v: str | None) -> str | None:
        if v is not None and not SECRET_REF_RE.match(v):
            raise ValueError(
                "secret_ref 는 'env:NAME' 또는 'file:<경로>' 형식이어야 합니다 (값 직접 입력 금지)"
            )
        return v

    @field_validator("base_url")
    @classmethod
    def _https_only(cls, v: str | None) -> str | None:
        if v is not None and not (
            v.startswith("https://") or v.startswith("http://127.0.0.1") or v.startswith("http://localhost")
        ):
            raise ValueError("base_url 은 https:// 이어야 합니다 (로컬 mock 은 http://127.0.0.1 허용)")
        return v

    @field_validator("extra_headers")
    @classmethod
    def _no_inline_secret_headers(cls, v: dict[str, str]) -> dict[str, str]:
        for k in v:
            if k.lower() in ("authorization", "x-api-key", "api-key"):
                raise ValueError(f"헤더 '{k}' 는 secret_ref 로만 주입할 수 있습니다")
        return v

    def missing_fields(self) -> list[str]:
        missing = []
        if not self.base_url:
            missing.append("base_url")
        if not self.model_id:
            missing.append("model_id")
        if not self.secret_ref:
            missing.append("secret_ref")
        if not self.approved_origins:
            missing.append("approved_origins")
        return missing


class AtlassianProductConfig(StrictModel):
    enabled: bool = False
    base_url: str | None = None
    deployment: Literal["cloud", "datacenter"] = "datacenter"
    api_version: str | None = None
    secret_ref: str | None = None
    ca_bundle: str | None = None
    project_key: str | None = None
    space_key: str | None = None
    repo_slug: str | None = None
    timeout_seconds: float = Field(30.0, gt=0, le=600)

    @field_validator("secret_ref")
    @classmethod
    def _secret_ref_format(cls, v: str | None) -> str | None:
        if v is not None and not SECRET_REF_RE.match(v):
            raise ValueError("secret_ref 는 'env:NAME' 또는 'file:<경로>' 형식이어야 합니다")
        return v


class IntegrationsConfig(StrictModel):
    write: bool = False  # 기본 off: preview/export 만
    approved_origins: list[str] = Field(default_factory=list)
    bitbucket: AtlassianProductConfig = Field(default_factory=lambda: AtlassianProductConfig())
    jira: AtlassianProductConfig = Field(default_factory=lambda: AtlassianProductConfig())
    confluence: AtlassianProductConfig = Field(default_factory=lambda: AtlassianProductConfig())
    bamboo: AtlassianProductConfig = Field(default_factory=lambda: AtlassianProductConfig())


class CadConfig(StrictModel):
    adapter: Literal["csv_json", "catia_v5_com", "3dexperience"] = "csv_json"
    field_mapping: dict[str, str] = Field(
        default_factory=dict, description="snapshot 필드 <- 회사 export 열 이름"
    )
    default_density_policy: Literal["reject", "use_material_table"] = "reject"
    material_table: str | None = None  # 회사 재료표 CSV 경로


class DocumentsConfig(StrictModel):
    approved_fonts: list[str] = Field(default_factory=list)
    renderer: Literal["none", "libreoffice"] = "none"
    renderer_path: str | None = None
    recalc_engine: Literal["none", "excel_com"] = "none"
    allow_hidden_content_to_llm: bool = False
    include_notes: bool = True
    max_index_file_mb: int = Field(200, ge=1, le=4096)


class ExtensionConfig(StrictModel):
    name: str
    path: str
    version: str
    compatible_schema: str  # 예: "4.0"
    entry: str = "register"


class SecurityConfig(StrictModel):
    forbid_symlinks: bool = True
    max_archive_members: int = Field(20000, ge=1)
    max_archive_ratio: int = Field(200, ge=1, description="압축 해제 확대 비율 상한")


class LoggingConfig(StrictModel):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    json_events: bool = True


class AppConfig(StrictModel):
    schema_version: str = "4.0"
    profile: Profile = Profile.CORP_OFFLINE
    paths: PathsConfig = Field(default_factory=lambda: PathsConfig())
    ml: MlConfig = Field(default_factory=lambda: MlConfig())
    gateway: GatewayConfig = Field(default_factory=lambda: GatewayConfig())
    integrations: IntegrationsConfig = Field(default_factory=lambda: IntegrationsConfig())
    cad: CadConfig = Field(default_factory=lambda: CadConfig())
    documents: DocumentsConfig = Field(default_factory=lambda: DocumentsConfig())
    extensions: list[ExtensionConfig] = Field(default_factory=list)
    security: SecurityConfig = Field(default_factory=lambda: SecurityConfig())
    logging: LoggingConfig = Field(default_factory=lambda: LoggingConfig())

    @model_validator(mode="after")
    def _profile_rules(self) -> AppConfig:
        if (
            self.profile in (Profile.CORP_OFFLINE, Profile.TRANSFER_TEST, Profile.PERSONAL_DEV)
            and self.gateway.enabled
        ):
            raise ValueError(
                f"프로파일 {self.profile.value} 에서는 gateway.enabled=true 를 허용하지 않습니다 (corp-gateway 전용)"
            )
        if self.profile is Profile.CORP_GATEWAY and self.gateway.enabled:
            missing = self.gateway.missing_fields()
            if missing:
                raise ValueError(
                    f"corp-gateway 프로파일에 필수 gateway 설정이 없습니다: {', '.join(missing)}"
                )
        return self

    def network_allowed(self) -> bool:
        return self.profile is Profile.CORP_GATEWAY and self.gateway.enabled
