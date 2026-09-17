from __future__ import annotations

from pathlib import Path

import pytest

from corp_dl_agent.config import load_config
from corp_dl_agent.config.loader import installation_defaults


def test_no_env_means_no_change() -> None:
    assert installation_defaults({}) == {}


def test_install_root_env_sets_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "설치 폴더"
    monkeypatch.setenv("DIA_INSTALL_ROOT", str(root))
    monkeypatch.delenv("DIA_DATA_ROOT", raising=False)
    cfg = load_config()
    assert Path(cfg.paths.install_root or "") == root.resolve()
    assert Path(cfg.paths.data_root) == (root / "workspace").resolve()
    assert Path(cfg.paths.company_root) == (root / "company").resolve()


def test_data_root_env_and_company_config_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "root"
    data = tmp_path / "work space"
    monkeypatch.setenv("DIA_INSTALL_ROOT", str(root))
    monkeypatch.setenv("DIA_DATA_ROOT", str(data))
    cfg = load_config()
    assert Path(cfg.paths.data_root) == data.resolve()
    # company config 가 있으면 환경변수보다 우선
    company = tmp_path / "company_config.yaml"
    other = tmp_path / "other data"
    company.write_text(f"profile: corp-offline\npaths:\n  data_root: '{other}'\n", encoding="utf-8")
    cfg2 = load_config(str(company))
    assert Path(cfg2.paths.data_root) == other.resolve()
    # CLI --set 은 company config 보다 우선
    cli = tmp_path / "cli data"
    cfg3 = load_config(str(company), overrides={"paths.data_root": str(cli)})
    assert Path(cfg3.paths.data_root) == cli.resolve()
