"""설치 root / data root / company root 의 폴더 구조 해석.

install_root/
  releases/<release_id>/<profile_id>/   불변 앱 + venv
  active.json                            활성 release/profile
  company/config|templates|extensions    회사 자료 (release 와 분리, 덮어쓰기 금지)
  workspace/data|runs|models|outputs|state|indexes   업무 데이터
  backups/
data_root 는 workspace 위치를 분리할 때 사용한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from corp_dl_agent.config.schemas import AppConfig

WORKSPACE_SUBDIRS = ("data", "runs", "models", "outputs", "state", "indexes", "logs")
COMPANY_SUBDIRS = ("config", "templates", "extensions")


@dataclass(frozen=True)
class Workspace:
    data_root: Path
    company_root: Path
    install_root: Path | None

    @classmethod
    def from_config(cls, cfg: AppConfig) -> Workspace:
        return cls(
            data_root=Path(cfg.paths.data_root).expanduser().resolve(),
            company_root=Path(cfg.paths.company_root).expanduser().resolve(),
            install_root=Path(cfg.paths.install_root).expanduser().resolve()
            if cfg.paths.install_root
            else None,
        )

    # workspace
    @property
    def data(self) -> Path:
        return self.data_root / "data"

    @property
    def runs(self) -> Path:
        return self.data_root / "runs"

    @property
    def models(self) -> Path:
        return self.data_root / "models"

    @property
    def outputs(self) -> Path:
        return self.data_root / "outputs"

    @property
    def state(self) -> Path:
        return self.data_root / "state"

    @property
    def indexes(self) -> Path:
        return self.data_root / "indexes"

    @property
    def logs(self) -> Path:
        return self.data_root / "logs"

    @property
    def backups(self) -> Path:
        return (self.install_root or self.data_root) / "backups"

    @property
    def state_db(self) -> Path:
        return self.state / "agent_state.sqlite"

    @property
    def index_db(self) -> Path:
        return self.indexes / "documents.sqlite"

    # company
    @property
    def company_config(self) -> Path:
        return self.company_root / "config"

    @property
    def company_templates(self) -> Path:
        return self.company_root / "templates"

    @property
    def company_extensions(self) -> Path:
        return self.company_root / "extensions"

    def ensure(self) -> None:
        for d in WORKSPACE_SUBDIRS:
            (self.data_root / d).mkdir(parents=True, exist_ok=True)

    def run_dir(self, run_id: str) -> Path:
        return self.runs / run_id

    def managed_roots(self) -> list[Path]:
        """이 프로그램이 삭제/쓰기 가능한 폴더. 이 밖의 임의 파일 삭제는 금지."""
        return [self.data_root, self.backups]
