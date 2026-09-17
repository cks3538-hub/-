"""로깅: 콘솔 + (선택) run 폴더 events.jsonl. 비밀값은 SecretRegistry 로 마스킹한다."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from corp_dl_agent.common import now_iso
from corp_dl_agent.security.secrets import REGISTRY


class MaskingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return REGISTRY.mask(super().format(record))


def setup_logging(level: str = "INFO") -> logging.Logger:
    root = logging.getLogger("corp_dl_agent")
    if not root.handlers:
        h = logging.StreamHandler()
        h.setFormatter(MaskingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(h)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    return root


class EventLog:
    """append-only JSON lines 이벤트 로그 (run 별 events.jsonl)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, event: str, **fields: Any) -> None:
        rec = {"ts": now_iso(), "event": event, **REGISTRY.mask_obj(fields)}
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")

    def read(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        out = []
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out
