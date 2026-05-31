import pytest


@pytest.fixture
def clean_env(monkeypatch):
    for var in ("HA_URL", "HA_TOKEN", "ANTHROPIC_API_KEY", "KOSHR_MODEL"):
        monkeypatch.delenv(var, raising=False)
    return monkeypatch
