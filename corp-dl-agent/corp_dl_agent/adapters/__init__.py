"""adapters: 외부 연결(LLM gateway / Atlassian / CAD) 과 등록 도구 dispatch.

원칙
- 이 패키지 최상위는 표준 라이브러리 + pydantic 만 사용한다. httpx/engineering/ml/documents 는 함수 안에서 lazy import.
- 네트워크 허용 여부는 `AppConfig.network_allowed()` 만 신뢰하며, 값이 없으면 public provider 로 fallback 하지 않는다.
- LLM 출력은 등록된 도구(adapters.tools.TOOL_REGISTRY) 의 strict 입력 모델로만 검증·실행한다. eval/exec/subprocess 로
  LLM 출력을 실행하지 않는다.
- 개인 PC 에서는 mock 서버 계약 시험까지만 가능하다 (MOCK_TESTED). live 연결은 사내에서 확인한다.
"""

from __future__ import annotations

__all__ = ["atlassian", "base", "cad", "llm", "tools"]
