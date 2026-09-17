"""프로파일별 정책 요약 (문서/doctor 출력용)."""

from __future__ import annotations

from corp_dl_agent.config.schemas import Profile

PROFILE_POLICY: dict[Profile, dict[str, str]] = {
    Profile.PERSONAL_DEV: {
        "data": "합성 데이터만",
        "network": "지정 공식 배포처의 개발 패키지 다운로드 가능 (앱 자체는 호출 없음)",
        "llm": "offline planner (gateway 비활성)",
    },
    Profile.TRANSFER_TEST: {
        "data": "합성 fixture",
        "network": "없음. 반입 ZIP 만으로 새 환경 설치·실행",
        "llm": "offline planner",
    },
    Profile.CORP_OFFLINE: {
        "data": "사내 로컬 파일",
        "network": "API 호출 0회 (outbound guard 활성)",
        "llm": "규칙 기반 offline planner",
    },
    Profile.CORP_GATEWAY: {
        "data": "사내 로컬 파일 + 최소 발췌",
        "network": "gateway.approved_origins 에 명시된 origin 만, TLS 검증, redirect 금지",
        "llm": "사내 endpoint/model ID/secret 참조가 모두 있을 때만",
    },
}
