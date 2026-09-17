"""secret 마스킹. 로그/예외/스냅샷에 비밀값이 섞이지 않도록 한다."""

from __future__ import annotations

import re
from typing import Any

_PATTERNS = [
    re.compile(r"(?i)(authorization\s*[:=]\s*)(bearer\s+)?[A-Za-z0-9\-._~+/]+=*"),
    re.compile(r"(?i)(x-api-key\s*[:=]\s*)[A-Za-z0-9\-._~+/]+=*"),
    re.compile(r"(?i)(api[_-]?key\s*[:=]\s*)['\"]?[A-Za-z0-9\-._~+/]{8,}=*"),
    re.compile(r"(?i)(token\s*[:=]\s*)['\"]?[A-Za-z0-9\-._~+/]{8,}=*"),
    re.compile(r"(?i)(password\s*[:=]\s*)['\"]?[^\s'\"]+"),
    re.compile(r"sk-[A-Za-z0-9]{8,}"),
]


class SecretRegistry:
    """해결된 secret 값을 등록하면 mask() 가 해당 값을 문자열에서 제거한다."""

    def __init__(self) -> None:
        self._values: set[str] = set()

    def register(self, value: str) -> None:
        if value and len(value) >= 4:
            self._values.add(value)

    def mask(self, text: str) -> str:
        out = text
        for v in sorted(self._values, key=len, reverse=True):
            out = out.replace(v, "***")
        for pat in _PATTERNS:
            out = pat.sub(lambda m: (m.group(1) if m.lastindex else "") + "***", out)
        return out

    def mask_obj(self, obj: Any) -> Any:
        if isinstance(obj, str):
            return self.mask(obj)
        if isinstance(obj, dict):
            return {k: self.mask_obj(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.mask_obj(v) for v in obj]
        return obj


REGISTRY = SecretRegistry()


def mask_text(text: str) -> str:
    return REGISTRY.mask(text)
