import json
import pytest
from datetime import date
import cost


def test_load_prices_from_explicit_path(tmp_path):
    f = tmp_path / "p.json"
    f.write_text(json.dumps({"as_of": "2026-05-28", "models": {"m": {"input": 1}}}))
    p = cost.load_prices(str(f))
    assert p["as_of"] == "2026-05-28"
    assert p["models"]["m"]["input"] == 1


def test_load_prices_env_override(tmp_path, monkeypatch):
    f = tmp_path / "env.json"
    f.write_text(json.dumps({"as_of": "2026-01-01", "models": {}}))
    monkeypatch.setenv("KOSHR_PRICES", str(f))
    assert cost.load_prices()["as_of"] == "2026-01-01"


def test_load_prices_default_fallback(monkeypatch):
    monkeypatch.delenv("KOSHR_PRICES", raising=False)
    p = cost.load_prices()
    assert p["as_of"] == "2026-05-28"
    assert "claude-sonnet-4-6" in p["models"]


SONNET = {"models": {"claude-sonnet-4-6": {
    "input": 3.00, "output": 15.00, "cache_write": 3.75, "cache_read": 0.30}}}


def test_price_all_buckets_and_savings():
    usage = {"input_tokens": 1000, "output_tokens": 1000,
             "cache_creation_input_tokens": 1000, "cache_read_input_tokens": 1000}
    c = cost.price(usage, "claude-sonnet-4-6", SONNET)
    assert c.input_cost == pytest.approx(0.003)
    assert c.output_cost == pytest.approx(0.015)
    assert c.cache_write_cost == pytest.approx(0.00375)
    assert c.cache_read_cost == pytest.approx(0.0003)
    assert c.total == pytest.approx(0.02205)
    assert c.cache_savings == pytest.approx(0.0027)


def test_price_missing_buckets_default_zero():
    c = cost.price({"input_tokens": 1000}, "claude-sonnet-4-6", SONNET)
    assert c.output_tokens == 0
    assert c.total == pytest.approx(0.003)


def test_price_unknown_model_returns_none():
    assert cost.price({"input_tokens": 1}, "no-such-model", SONNET) is None


def test_days_old():
    assert cost.days_old("2026-05-28", today=date(2026, 8, 10)) == 74


def test_is_stale_threshold():
    assert cost.is_stale("2026-05-28", 60, today=date(2026, 8, 10)) is True
    assert cost.is_stale("2026-05-28", 60, today=date(2026, 6, 1)) is False
