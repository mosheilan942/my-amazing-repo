from unittest.mock import MagicMock

import pytest

import koshr

SONNET = {"as_of": "2026-05-28", "models": {"claude-sonnet-4-6": {
    "input": 3.00, "output": 15.00, "cache_write": 3.75, "cache_read": 0.30}}}


def test_report_cost_prices_and_records(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("KOSHR_LEDGER", str(tmp_path / "led.jsonl"))
    fake_brain = MagicMock()
    fake_brain.name = "claude"
    fake_brain.model = "claude-sonnet-4-6"
    fake_brain.last_usage = {"input_tokens": 1000, "output_tokens": 1000,
                             "cache_creation_input_tokens": 1000, "cache_read_input_tokens": 1000}
    c = koshr.report_cost(fake_brain, "turn off boiler", SONNET)
    assert c.total == pytest.approx(0.02205)
    assert "cost:" in capsys.readouterr().out
    assert (tmp_path / "led.jsonl").read_text().count("\n") == 1


def test_report_cost_demo_records_zero(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("KOSHR_LEDGER", str(tmp_path / "led.jsonl"))
    fake_brain = MagicMock()
    fake_brain.name = "demo"
    fake_brain.model = None
    fake_brain.last_usage = None
    c = koshr.report_cost(fake_brain, "lights", SONNET)
    assert c is None
    assert "no API call" in capsys.readouterr().out
    assert (tmp_path / "led.jsonl").read_text().count("\n") == 1


def test_report_cost_api_ran_but_no_prices(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("KOSHR_LEDGER", str(tmp_path / "led.jsonl"))
    fake_brain = MagicMock()
    fake_brain.name = "claude"
    fake_brain.model = "claude-sonnet-4-6"
    fake_brain.last_usage = {"input_tokens": 100, "output_tokens": 50,
                             "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
    c = koshr.report_cost(fake_brain, "boiler", None)
    assert c is None
    assert "not computed" in capsys.readouterr().out
