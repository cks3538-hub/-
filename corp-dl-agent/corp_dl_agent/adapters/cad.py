"""CAD adapter 계약: CSV/JSON snapshot 수입(구현·검증됨) 과 live 추출(CATIA V5 COM / 3DEXPERIENCE, INACTIVE).

원칙 (BUILD_SPEC [11], ARCHITECTURE_V4)
- CSV/JSON 수입은 engineering.import_snapshot 에 위임한다 (실제 구현).
- CATIA V5 Windows Automation 과 3DEXPERIENCE 는 서로 다른 adapter 다. 개인 PC 에는 CATIA 가 없으므로
  실제 연결을 했다고 주장하지 않는다: available() 는 False, status() 는 INACTIVE(BLOCKED) 사유, extract() 는
  E_NOT_SUPPORTED. 검증 가능한 설치 문서 없이는 object model 의 method 이름을 창작하지 않는다.
- commands/cad_cmd.py 는 이 모듈을 importlib 로 탐색하여 CatiaV5ComAdapter/ThreeDExperienceAdapter 를
  무인자 생성한 뒤 available() 를 호출한다. 이 인터페이스는 유지해야 한다.
"""

from __future__ import annotations

import platform
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Literal, Protocol, runtime_checkable

from corp_dl_agent.common import Status, StatusRecord, now_iso
from corp_dl_agent.errors import AgentError

if TYPE_CHECKING:  # engineering 은 실행 시 lazy import (tools.py 와 동일 규칙)
    from corp_dl_agent.engineering.cad_snapshot import CadSnapshot

AdapterKind = Literal["csv_json", "catia_v5_com", "3dexperience"]
INACTIVE = "INACTIVE"


@runtime_checkable
class CadAdapter(Protocol):
    """모든 CAD adapter 가 따르는 계약."""

    name: ClassVar[str]
    live: ClassVar[bool]

    def available(self) -> bool: ...

    def status(self) -> StatusRecord: ...

    def extract(
        self,
        source: str | Path | None = None,
        *,
        field_mapping: dict[str, str] | None = None,
        strict: bool = True,
    ) -> CadSnapshot: ...


class CsvJsonCadAdapter:
    """CSV(UTF-8/UTF-8-SIG/CP949)/JSON export 파일을 CadSnapshot 으로 수입한다 (engineering.import_snapshot 위임)."""

    name: ClassVar[str] = "csv_json"
    live: ClassVar[bool] = False

    def __init__(self, *, delimiter: str = ",") -> None:
        self.delimiter = delimiter

    def available(self) -> bool:
        try:
            import corp_dl_agent.engineering.cad_snapshot  # noqa: F401
        except ImportError:
            return False
        return True

    def status(self) -> StatusRecord:
        if not self.available():
            return StatusRecord(
                status=Status.BLOCKED,
                reason="engineering.cad_snapshot 모듈을 로드할 수 없습니다",
                checked_at=now_iso(),
            )
        return StatusRecord(
            status=Status.PASS,
            reason="CSV/JSON snapshot 수입 경로 (원본 파일은 읽기 전용, 수정하지 않음)",
            evidence=["engineering.import_snapshot"],
            checked_at=now_iso(),
        )

    def extract(
        self,
        source: str | Path | None = None,
        *,
        field_mapping: dict[str, str] | None = None,
        strict: bool = True,
    ) -> CadSnapshot:
        if source is None:
            raise AgentError("E_INPUT_INVALID", "csv_json adapter 에는 입력 파일 경로(source) 가 필요합니다")
        try:
            from corp_dl_agent.engineering.cad_snapshot import import_snapshot
        except ImportError as exc:
            from corp_dl_agent.errors import blocked_dependency

            raise blocked_dependency("corp_dl_agent.engineering", "cad.extract") from exc
        return import_snapshot(
            Path(source), field_mapping=field_mapping or None, strict=strict, delimiter=self.delimiter
        )


class _InactiveLiveAdapter:
    """live adapter 공통: 이 배포에서는 항상 INACTIVE.

    available() 는 설치/COM/라이선스/API 제공 여부를 이 환경에서 검증할 수 없으므로 False 를 반환한다.
    실제 연결 코드는 사내 Windows PC 에서 아래 확인 항목을 검증한 뒤 별도 릴리스로 추가한다.
    """

    name: ClassVar[str] = ""
    live: ClassVar[bool] = True
    label_ko: ClassVar[str] = ""
    site_checklist_ko: ClassVar[tuple[str, ...]] = ()

    def __init__(self) -> None:  # 무인자 생성 (cad_cmd 가 cls() 로 탐색)
        pass

    def available(self) -> bool:
        return False

    def inactive_reasons(self) -> list[str]:
        reasons: list[str] = []
        if platform.system() != "Windows":
            reasons.append(
                f"{platform.system()} 에서는 {self.label_ko} 연결을 지원하지 않습니다 (Windows 전용)"
            )
        reasons.append(f"{self.label_ko} 설치/자동화 API/라이선스가 이 환경에서 확인되지 않았습니다")
        reasons.append("이 릴리스에는 검증된 live 추출 구현이 포함되어 있지 않습니다 (계약만 제공)")
        return reasons

    def status(self) -> StatusRecord:
        return StatusRecord(
            status=Status.BLOCKED,
            reason=f"{INACTIVE}: " + "; ".join(self.inactive_reasons()),
            evidence=[f"site_checklist: {item}" for item in self.site_checklist_ko],
            checked_at=now_iso(),
        )

    def extract(
        self,
        source: str | Path | None = None,
        *,
        field_mapping: dict[str, str] | None = None,
        strict: bool = True,
    ) -> CadSnapshot:
        raise AgentError(
            "E_NOT_SUPPORTED",
            f"{self.label_ko} live 추출은 이 환경에서 지원되지 않습니다 ({INACTIVE}). "
            "CAD 에서 CSV/JSON 으로 export 한 뒤 'cad import' 로 수입하세요.",
            hint="사내 Windows PC 에서 설치/자동화 API/라이선스를 확인하고 adapter 계약을 검증한 뒤 활성화합니다.",
            details={
                "adapter": self.name,
                "status": INACTIVE,
                "status_record": self.status().model_dump(mode="json"),
                "site_checklist": list(self.site_checklist_ko),
                "fallback": "cad import --input <export.csv|json> --output cad_snapshot.json",
            },
        )

    def describe(self) -> dict[str, Any]:
        return {
            "adapter": self.name,
            "label": self.label_ko,
            "live": True,
            "available": self.available(),
            "status": self.status().model_dump(mode="json"),
            "site_checklist": list(self.site_checklist_ko),
        }


class CatiaV5ComAdapter(_InactiveLiveAdapter):
    """CATIA V5 Windows Automation(COM) adapter 계약 — 이 릴리스에서는 INACTIVE.

    현장(사내 Windows PC) 확인 항목:
    - CATIA V5 설치 여부·릴리스/서비스팩, 자동화(Automation) 사용 허가·라이선스
    - COM 서버 등록 여부(레지스트리 ProgID) 와 실행 중인 세션에 연결할지/새로 띄울지 정책
    - pywin32 반입 승인 (optional-dependency 'cad-windows')
    - 실제 object model: 문서/제품 트리 순회, 부품 참조·instance 경로, 재료/밀도·부피(관성 측정) 읽기 방법과
      단위 설정 — 설치 제품의 Automation 문서로 확인한다 (여기서 method 이름을 추정·창작하지 않는다)
    - 읽기 전용 접근 보장 (원본 CATPart/CATProduct 저장·수정 금지)
    """

    name: ClassVar[str] = "catia_v5_com"
    label_ko: ClassVar[str] = "CATIA V5 Windows Automation (COM)"
    site_checklist_ko: ClassVar[tuple[str, ...]] = (
        "CATIA V5 설치·릴리스·서비스팩·자동화 라이선스 확인",
        "COM 서버 등록(ProgID) 및 세션 연결 정책 확인",
        "pywin32 반입 승인 (optional-dependency cad-windows)",
        "설치 제품 Automation 문서로 object model(트리 순회/재료/부피/단위) 확인",
        "읽기 전용 접근 보장 (원본 저장·수정 금지)",
    )


class ThreeDExperienceAdapter(_InactiveLiveAdapter):
    """3DEXPERIENCE adapter 계약 — 이 릴리스에서는 INACTIVE.

    현장 확인 항목:
    - 제품/버전(온프레미스·클라우드), 제공되는 자동화/웹 서비스 API 와 인증 방식
    - 사용자 권한(읽기 전용) 과 데이터 반출 정책
    - 부품 참조·instance 경로·재료·부피 조회 방법과 단위 — 제공 문서로 확인한다 (추정 금지)
    """

    name: ClassVar[str] = "3dexperience"
    label_ko: ClassVar[str] = "3DEXPERIENCE"
    site_checklist_ko: ClassVar[tuple[str, ...]] = (
        "제품/버전(온프레미스·클라우드) 과 제공 API·인증 방식 확인",
        "읽기 전용 사용자 권한 및 데이터 반출 정책 확인",
        "부품 참조/instance 경로/재료/부피 조회 방법과 단위 확인 (제공 문서 기준)",
    )


ADAPTERS: dict[str, type[Any]] = {
    "csv_json": CsvJsonCadAdapter,
    "catia_v5_com": CatiaV5ComAdapter,
    "3dexperience": ThreeDExperienceAdapter,
}


def get_adapter(kind: str) -> CadAdapter:
    cls = ADAPTERS.get(kind)
    if cls is None:
        raise AgentError(
            "E_NOT_SUPPORTED", f"알 수 없는 CAD adapter: {kind}", details={"allowed": sorted(ADAPTERS)}
        )
    adapter = cls()
    assert isinstance(adapter, CadAdapter)
    return adapter


def adapter_status(kind: str) -> StatusRecord:
    return get_adapter(kind).status()


def all_adapter_status() -> dict[str, dict[str, Any]]:
    """doctor 용: 모든 adapter 의 available/status (연결 시도 없음)."""
    out: dict[str, dict[str, Any]] = {}
    for kind in ADAPTERS:
        a = get_adapter(kind)
        out[kind] = {
            "live": bool(a.live),
            "available": bool(a.available()),
            "status": a.status().model_dump(mode="json"),
        }
    return out


__all__ = [
    "ADAPTERS",
    "INACTIVE",
    "AdapterKind",
    "CadAdapter",
    "CatiaV5ComAdapter",
    "CsvJsonCadAdapter",
    "ThreeDExperienceAdapter",
    "adapter_status",
    "all_adapter_status",
    "get_adapter",
]
