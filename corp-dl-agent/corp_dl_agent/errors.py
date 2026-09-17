"""오류 코드·한국어 메시지·종료 코드.

모든 사용자 노출 오류는 AgentError 를 사용한다. 비밀값(토큰/키/비밀번호)은 절대 message/details 에 넣지 않는다.
"""

from __future__ import annotations

from typing import Any

# 프로세스 종료 코드 (CLI/스크립트 공통)
EXIT_OK = 0
EXIT_USAGE = 2
EXIT_BLOCKED_CONFIG = 3
EXIT_BLOCKED_DEPENDENCY = 4
EXIT_VALIDATION = 5
EXIT_RUNTIME = 6
EXIT_BUDGET = 7
EXIT_BLOCKED_PREREQUISITE = 8
EXIT_NOT_SUPPORTED = 9

# 오류 코드 -> (한국어 요약, 기본 해결 힌트, 종료 코드)
ERROR_CATALOG: dict[str, tuple[str, str, int]] = {
    "E_CONFIG_INVALID": (
        "설정 파일이 올바르지 않습니다.",
        "config validate 명령으로 오류 위치를 확인하고 schema/config.schema.json 을 참고하세요.",
        EXIT_BLOCKED_CONFIG,
    ),
    "E_CONFIG_UNKNOWN_KEY": (
        "설정에 허용되지 않은 키가 있습니다.",
        "오타 또는 지원되지 않는 항목입니다. 키를 제거하거나 문서를 확인하세요.",
        EXIT_BLOCKED_CONFIG,
    ),
    "E_CONFIG_SECRET_MISSING": (
        "secret 참조를 해결할 수 없습니다.",
        "환경 변수 또는 지정된 secret 파일이 있는지 확인하세요. 값을 설정 파일에 직접 쓰지 마세요.",
        EXIT_BLOCKED_CONFIG,
    ),
    "E_BLOCKED_CONFIG": (
        "필수 설정이 없어 기능이 차단되었습니다.",
        "회사 설정(company/config)에 필요한 값을 채운 뒤 다시 시도하세요.",
        EXIT_BLOCKED_CONFIG,
    ),
    "E_BLOCKED_DEPENDENCY": (
        "필요한 패키지가 설치되어 있지 않습니다.",
        "설치 프로파일(ml-cpu/documents)이 포함된 release 로 설치했는지 확인하세요.",
        EXIT_BLOCKED_DEPENDENCY,
    ),
    "E_BLOCKED_PREREQUISITE": (
        "선행 조건(Python/pip/venv 등)이 충족되지 않았습니다.",
        "회사 승인 Python 경로를 --python 인자로 지정하거나 관리자에게 승인 Python 설치를 요청하세요.",
        EXIT_BLOCKED_PREREQUISITE,
    ),
    "E_NOT_SUPPORTED": (
        "이 환경에서는 지원되지 않는 기능입니다.",
        "지원 조건(OS/설치 제품/승인 엔진)을 문서에서 확인하세요.",
        EXIT_NOT_SUPPORTED,
    ),
    "E_INPUT_INVALID": (
        "입력 데이터가 올바르지 않습니다.",
        "오류 세부 정보의 행/열/필드를 수정한 뒤 다시 실행하세요. 오류 행은 자동 삭제되지 않습니다.",
        EXIT_VALIDATION,
    ),
    "E_SCHEMA_INVALID": (
        "데이터가 schema 와 일치하지 않습니다.",
        "schemas/ 폴더의 JSON schema 와 비교하세요.",
        EXIT_VALIDATION,
    ),
    "E_PATH_OUTSIDE_ROOT": (
        "승인된 입력/출력 폴더 밖의 경로입니다.",
        "config 의 paths 항목에 허용된 폴더 안의 경로만 사용하세요.",
        EXIT_VALIDATION,
    ),
    "E_PATH_LINK": (
        "symlink/junction 경로는 허용되지 않습니다.",
        "실제 폴더 경로를 사용하세요.",
        EXIT_VALIDATION,
    ),
    "E_UNIT_MISMATCH": (
        "단위가 일치하지 않거나 알 수 없는 단위입니다.",
        "units 항목에 지원 단위(mm3, m3, g/cm3, kg/m3 등)를 지정하세요.",
        EXIT_VALIDATION,
    ),
    "E_MISSING_VALUE": (
        "필수 값이 없어 계산할 수 없습니다.",
        "값이 없는 항목은 MISSING 으로 표시됩니다. 값을 입력하거나 scenario 가정을 명시하세요.",
        EXIT_VALIDATION,
    ),
    "E_REVISION_MISMATCH": (
        "설계안 비교 조건(revision/기준일/단위)이 일치하지 않습니다.",
        "동일 revision·기준일·단위로 정렬한 뒤 비교하세요.",
        EXIT_VALIDATION,
    ),
    "E_SPLIT_IMPOSSIBLE": (
        "유효한 그룹 분할을 만들 수 없습니다.",
        "group_column 값이 충분히 다양한지 확인하세요. 행 무작위 분할로 대체하지 않습니다.",
        EXIT_VALIDATION,
    ),
    "E_LEAKAGE": (
        "데이터 누수 위험이 감지되었습니다.",
        "ID/사후 결과/target 파생 열을 feature 에서 제외하세요.",
        EXIT_VALIDATION,
    ),
    "E_MEMORY_LIMIT": (
        "dense 변환 결과가 메모리 상한을 초과합니다.",
        "행 수/feature 수(범주 수)를 줄이거나 max_dense_bytes 상한을 검토하세요.",
        EXIT_VALIDATION,
    ),
    "E_STATE_TRANSITION": (
        "허용되지 않은 상태 전이입니다.",
        "status 명령으로 현재 상태를 확인하세요.",
        EXIT_RUNTIME,
    ),
    "E_DB_LOCKED": (
        "상태 DB 가 다른 프로세스에 의해 잠겨 있습니다.",
        "동시에 실행 중인 다른 작업이 끝날 때까지 기다린 뒤 다시 시도하세요. 상태 DB 는 로컬 디스크에 두어야 합니다.",
        EXIT_RUNTIME,
    ),
    "E_LEASE_HELD": (
        "다른 worker 가 실행 중입니다.",
        "heartbeat 만료 후 다시 시도하거나 해당 worker 를 종료하세요.",
        EXIT_RUNTIME,
    ),
    "E_FINGERPRINT_CHANGED": (
        "코드/데이터/lock 이 변경되어 자동 재개할 수 없습니다.",
        "새 run 으로 시작하세요.",
        EXIT_RUNTIME,
    ),
    "E_BUDGET_EXCEEDED": (
        "시간/호출/토큰 예산을 초과했습니다.",
        "resource_budget 을 확인하세요. 예산은 완료 보증이 아닙니다.",
        EXIT_BUDGET,
    ),
    "E_CHECKPOINT_CORRUPT": (
        "checkpoint 가 손상되었거나 검증에 실패했습니다.",
        "latest 대신 best checkpoint 로 재개하거나 새 run 을 시작하세요.",
        EXIT_RUNTIME,
    ),
    "E_ARTIFACT_UNTRUSTED": (
        "신뢰할 수 없는 artifact 입니다.",
        "이 프로그램이 생성하고 checksum 이 일치하는 artifact 만 로드합니다.",
        EXIT_VALIDATION,
    ),
    "E_ARTIFACT_SYNTHETIC": (
        "합성(synthetic) 모델은 운영 자동 선택에서 제외됩니다.",
        "실데이터로 학습한 모델(data_origin=corporate)을 지정하세요.",
        EXIT_VALIDATION,
    ),
    "E_NEEDS_ACCEPTANCE_CRITERIA": (
        "업무 허용오차(acceptance)가 입력되지 않았습니다.",
        "TaskSpec.acceptance 에 허용오차를 입력하세요. 임의로 생성하지 않습니다.",
        EXIT_VALIDATION,
    ),
    "E_NETWORK_BLOCKED": (
        "현재 프로파일에서는 네트워크 호출이 허용되지 않습니다.",
        "corp-gateway 프로파일과 approved origin 설정이 필요합니다.",
        EXIT_BLOCKED_CONFIG,
    ),
    "E_ORIGIN_NOT_ALLOWED": (
        "허용되지 않은 origin 으로의 요청입니다.",
        "gateway.approved_origins 에 등록된 주소만 호출됩니다.",
        EXIT_BLOCKED_CONFIG,
    ),
    "E_GATEWAY_AUTH": (
        "gateway 인증에 실패했습니다(401/403).",
        "secret 참조와 권한을 확인하세요. 인증 오류는 재시도하지 않습니다.",
        EXIT_RUNTIME,
    ),
    "E_GATEWAY_RESPONSE": (
        "gateway 응답이 계약과 일치하지 않습니다.",
        "model ID/응답 형식을 사내 담당자와 확인하세요.",
        EXIT_RUNTIME,
    ),
    "E_LLM_PLAN_INVALID": (
        "LLM 계획 JSON 이 허용 범위를 벗어났습니다.",
        "등록된 도구/범위만 허용됩니다. 교정은 1회만 시도합니다.",
        EXIT_VALIDATION,
    ),
    "E_TOOL_NOT_REGISTERED": (
        "등록되지 않은 도구 호출입니다.",
        "adapters/tools.py 의 등록 도구만 호출할 수 있습니다.",
        EXIT_VALIDATION,
    ),
    "E_DOC_UNSUPPORTED": (
        "지원되지 않는 문서 형식/요소입니다.",
        "구형 .ppt/.xls 는 승인 변환이 필요합니다. 호환성 보고서를 확인하세요.",
        EXIT_NOT_SUPPORTED,
    ),
    "E_DOC_PAYLOAD_MISMATCH": (
        "문서 수치가 report_payload 와 일치하지 않습니다.",
        "validation_report.json 의 항목을 확인하세요.",
        EXIT_VALIDATION,
    ),
    "E_DOC_STRUCTURE_CHANGED": (
        "템플릿 구조가 보존되지 않았습니다.",
        "승인된 placeholder/range 만 수정되는지 확인하세요.",
        EXIT_VALIDATION,
    ),
    "E_PACKAGE_INVALID": (
        "반입 패키지 검증에 실패했습니다.",
        "manifest/checksums/파일 목록 불일치 항목을 확인하세요. 패키지를 다시 받으세요.",
        EXIT_VALIDATION,
    ),
    "E_INSTALL_FAILED": (
        "설치에 실패했습니다. 기존 활성 버전은 유지됩니다.",
        "install 로그의 누락 wheel/hash/ABI 항목을 확인하세요.",
        EXIT_RUNTIME,
    ),
    "E_UPGRADE_LOCKED": (
        "실행 중인 작업이 있어 업데이트를 시작할 수 없습니다.",
        "작업 완료 또는 pause 후 다시 시도하세요.",
        EXIT_RUNTIME,
    ),
    "E_ROLLBACK_INCOMPATIBLE": (
        "구버전이 현재 DB schema 를 읽을 수 없습니다.",
        "DB snapshot 복구가 필요하며 업데이트 이후 이력이 손실될 수 있습니다. 문서를 확인하세요.",
        EXIT_RUNTIME,
    ),
    "E_INTEGRATION_WRITE_DISABLED": (
        "원격 쓰기가 비활성화되어 있습니다(integrations.write=false).",
        "preview/export 만 가능합니다. 쓰기는 명시적 설정과 승인이 필요합니다.",
        EXIT_BLOCKED_CONFIG,
    ),
    "E_INTEGRATION_CONFLICT": (
        "원격 리소스 충돌(409)이 보고되었습니다.",
        "대상이 이미 존재하거나 버전이 바뀌었습니다. 원격 상태를 확인한 뒤 payload 를 갱신하세요. 자동 덮어쓰기하지 않습니다.",
        EXIT_RUNTIME,
    ),
    "E_INTERNAL": (
        "내부 오류가 발생했습니다.",
        "로그를 확인하고 재현 가능한 합성 입력으로 보고하세요.",
        EXIT_RUNTIME,
    ),
    "E_USAGE": ("명령 사용법이 올바르지 않습니다.", "--help 를 확인하세요.", EXIT_USAGE),
}


class AgentError(Exception):
    """사용자에게 보고되는 오류. code 는 ERROR_CATALOG 의 키."""

    def __init__(
        self,
        code: str,
        message: str | None = None,
        *,
        hint: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        if code not in ERROR_CATALOG:
            code = "E_INTERNAL"
        summary, default_hint, exit_code = ERROR_CATALOG[code]
        self.code = code
        self.summary = summary
        self.message = message or summary
        self.hint = hint or default_hint
        self.details = details or {}
        self.exit_code = exit_code
        super().__init__(f"[{code}] {self.message}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "summary": self.summary,
            "message": self.message,
            "hint": self.hint,
            "details": self.details,
            "exit_code": self.exit_code,
        }

    def format_ko(self) -> str:
        lines = [f"오류 {self.code}: {self.message}"]
        if self.hint:
            lines.append(f"해결 방법: {self.hint}")
        for k, v in self.details.items():
            lines.append(f"  - {k}: {v}")
        return "\n".join(lines)


def blocked_dependency(package: str, feature: str) -> AgentError:
    return AgentError(
        "E_BLOCKED_DEPENDENCY",
        f"'{feature}' 기능에 필요한 패키지 '{package}' 가 설치되어 있지 않습니다.",
        details={"package": package, "feature": feature},
    )
