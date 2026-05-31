import json

import pytest

import cost
import ledger

SONNET = {"models": {"claude-sonnet-4-6": {
    "input": 3.00, "output": 15.00, "cache_write": 3.75, "cache_read": 0.30}}}


def _cost():
    return cost.price({"input_tokens": 1000, "output_tokens": 1000,
                       "cache_creation_input_tokens": 1000, "cache_read_input_tokens": 1000},
                      "claude-sonnet-4-6", SONNET)


def test_record_writes_one_jsonl_line(tmp_path):
    p = tmp_path / "led.jsonl"
    ledger.record(_cost(), "turn off boiler", "claude", path=str(p))
    lines = p.read_text().splitlines()
    assert len(lines) == 1
    e = json.loads(lines[0])
    assert e["brain"] == "claude"
    assert e["model"] == "claude-sonnet-4-6"
    assert e["command"] == "turn off boiler"
    assert e["total"] == pytest.approx(0.02205)
    assert "ts" in e


def test_record_none_cost_logs_zero(tmp_path):
    p = tmp_path / "led.jsonl"
    ledger.record(None, "demo cmd", "demo", path=str(p))
    e = json.loads(p.read_text().splitlines()[0])
    assert e["model"] is None
    assert e["total"] == 0.0


def test_summarize_aggregates(tmp_path):
    p = tmp_path / "led.jsonl"
    ledger.record(_cost(), "a", "claude", path=str(p))
    ledger.record(_cost(), "b", "claude", path=str(p))
    ledger.record(None, "c", "demo", path=str(p))
    s = ledger.summarize(str(p))
    assert s["requests"] == 3
    assert s["by_brain"] == {"claude": 2, "demo": 1}
    assert s["total_cost"] == pytest.approx(0.0441)
    assert s["avg_cost"] == pytest.approx(0.0441 / 3)
    assert s["cache_savings"] == pytest.approx(0.0054)


def test_summarize_missing_file(tmp_path):
    s = ledger.summarize(str(tmp_path / "nope.jsonl"))
    assert s["requests"] == 0
    assert s["total_cost"] == 0.0


def test_summarize_skips_corrupt_lines(tmp_path):
    p = tmp_path / "led.jsonl"
    ledger.record(_cost(), "a", "claude", path=str(p))
    with open(p, "a") as f:
        f.write("not json\n")
    s = ledger.summarize(str(p))
    assert s["requests"] == 1
    assert s["skipped"] == 1
