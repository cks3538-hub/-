"""corp_dl_agent - 설계·문서 업무 자동화 범용 코어.

패키지 최상위에서는 third-party 모듈을 import하지 않는다.
(표준 라이브러리 preflight/verifier 및 doctor가 torch/pandas 없이도 동작해야 한다.)
"""

from corp_dl_agent.version import __version__, SCHEMA_VERSION, CALCULATION_VERSION

__all__ = ["__version__", "SCHEMA_VERSION", "CALCULATION_VERSION"]
