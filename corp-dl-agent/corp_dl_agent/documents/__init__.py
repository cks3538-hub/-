"""documents: PPTX/XLSX 색인·근거 검색·report_payload 기반 템플릿 채우기·검증.

모듈:
- index      허용 root 의 PPTX/XLSX 를 sqlite 에 색인 (slide/shape/table/notes/chart, sheet/cell/named range/table)
- search     키워드/metadata 검색 (embedding 없음), hidden 제외 기본
- evidence   검색 결과 -> evidence.json
- payload    report_payload.json schema + 합계/차이 재검증 (validate_payload)
- templates  템플릿 복사본의 승인 placeholder/named range 만 채우기 (fill_pptx / fill_xlsx)
- validation 생성 문서 검증 (수치 일치·구조 보존·오래된 값 잔존·RECALC/RENDER 상태)
- synthetic  합성 fixture 문서 생성

규칙: python-pptx / openpyxl 은 각 함수 안에서 lazy import 하며, 없으면 errors.blocked_dependency 를 raise 한다.
매크로/OLE/외부 링크는 실행하지 않고 flag 만 남긴다. 수식 재계산·렌더링은 수행하지 않으며
RECALC_NOT_RUN / RENDER_NOT_RUN 상태를 명시한다.
"""

from __future__ import annotations

__all__ = ["evidence", "index", "payload", "search", "synthetic", "templates", "validation"]
