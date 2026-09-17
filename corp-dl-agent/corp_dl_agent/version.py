"""버전 상수. pyproject.toml [project].version 과 동일해야 한다 (tests/test_version.py 에서 검사)."""

__version__ = "4.0.0"
# 산출물/설정/DB schema 호환 범위 판단에 사용. 호환되지 않는 변경 시 major 증가.
SCHEMA_VERSION = "4.0"
# 중량/원가 계산식 버전. 값 provenance 에 기록된다.
CALCULATION_VERSION = "mass-cost-4.0"
DB_SCHEMA_VERSION = 4
