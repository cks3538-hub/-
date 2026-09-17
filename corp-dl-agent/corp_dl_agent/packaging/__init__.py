"""packaging: 타깃 프로파일, lock/wheelhouse 검증, dependency inventory, release manifest, 반입 ZIP 빌더.

표준 라이브러리 + pydantic 만 사용한다. 검증 코어(파서/검사기)는 `verifier_core.py` 에 있으며
`scripts/_common.py` 와 byte 단위로 동일하다 (앱 미설치 상태의 스크립트가 같은 로직을 쓴다).
"""

from corp_dl_agent.packaging.manifest import ReleaseManifest
from corp_dl_agent.packaging.target import TargetProfile, detect_host, get_profile

__all__ = ["TargetProfile", "detect_host", "get_profile", "ReleaseManifest"]
