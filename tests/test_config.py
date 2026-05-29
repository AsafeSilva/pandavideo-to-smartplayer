import pytest
from pathlib import Path

from src.config import Settings


def test_settings_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("PANDA_API_KEY", "panda-key")
    monkeypatch.setenv("SMARTPLAYER_CLIENT_ID", "cid")
    monkeypatch.setenv("SMARTPLAYER_CLIENT_SECRET", "csec")
    monkeypatch.setenv("SMARTPLAYER_USER_CODE", "uc")
    monkeypatch.setenv("MAX_DOWNLOAD_CONCURRENCY", "5")

    # Passa dotenv_path inexistente para evitar que load_dotenv() carregue o .env real
    s = Settings.from_env(dotenv_path=tmp_path / "nonexistent.env")
    assert s.panda_api_key == "panda-key"
    assert s.sp_client_id == "cid"
    assert s.max_download_concurrency == 5
    assert s.max_upload_concurrency == 3  # default
    assert s.panda_quality == "original"  # default


def test_settings_missing_required(monkeypatch):
    monkeypatch.setenv("PANDA_API_KEY", "")  # string vazia — _require() rejeita; delenv seria recarregado pelo load_dotenv()
    monkeypatch.setenv("SMARTPLAYER_CLIENT_ID", "x")
    monkeypatch.setenv("SMARTPLAYER_CLIENT_SECRET", "x")
    monkeypatch.setenv("SMARTPLAYER_USER_CODE", "x")
    with pytest.raises(SystemExit) as exc:
        Settings.from_env()
    assert "PANDA_API_KEY" in str(exc.value)
