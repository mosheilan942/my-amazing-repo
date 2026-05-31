import json

import pytest

import brain
from brain import DemoBrain, Draft, select

SENSORS = [
    "sensor.jewish_calendar_upcoming_shabbat_candle_lighting",
    "sensor.jewish_calendar_upcoming_shabbat_havdalah",
]


def test_demobrain_three_commands_produce_valid_drafts():
    b = DemoBrain(SENSORS)
    for cmd in ["turn off the boiler before candle lighting",
                "turn on the hallway lights when Shabbat ends",
                "set the AC to 22 degrees an hour before Shabbat"]:
        d = b.draft(cmd)
        assert isinstance(d, Draft)
        assert d.alias and d.summary
        assert d.action[0]["target"]["entity_id"] == "input_boolean.demo_switch"
        json.dumps(d.body())  # body must be JSON-serializable


def test_demobrain_uses_discovered_sensor_ids():
    b = DemoBrain(["sensor.jewish_calendar_my_candle_lighting",
                   "sensor.jewish_calendar_my_havdalah"])
    d = b.draft("turn on the hallway lights when Shabbat ends")
    assert d.trigger[0]["at"] == "sensor.jewish_calendar_my_havdalah"


def test_demobrain_unknown_command_raises():
    b = DemoBrain(SENSORS)
    with pytest.raises(ValueError):
        b.draft("play some music")


from unittest.mock import MagicMock, patch


def test_select_picks_brain_by_env(clean_env):
    assert isinstance(select(SENSORS), DemoBrain)
    clean_env.setenv("ANTHROPIC_API_KEY", "sk-test")
    with patch("anthropic.Anthropic"):
        assert select(SENSORS).name == "claude"


def test_claudebrain_parses_tool_output(clean_env):
    clean_env.setenv("ANTHROPIC_API_KEY", "sk-test")
    tool_block = MagicMock(type="tool_use", input={
        "alias": "Boiler off",
        "trigger": [{"platform": "time", "at": "sensor.x"}],
        "action": [{"service": "input_boolean.turn_off",
                    "target": {"entity_id": "input_boolean.demo_switch"}}],
        "summary": "Turns the boiler off.",
    })
    fake_resp = MagicMock(content=[tool_block])
    with patch("anthropic.Anthropic") as Anth:
        Anth.return_value.messages.create.return_value = fake_resp
        d = brain.ClaudeBrain(SENSORS).draft("turn off the boiler")
    assert d.alias == "Boiler off"
    assert d.summary == "Turns the boiler off."
    assert d.mode == "single"  # default applied


def test_claudebrain_records_last_usage(clean_env):
    clean_env.setenv("ANTHROPIC_API_KEY", "sk-test")
    tool_block = MagicMock(type="tool_use", input={
        "alias": "x", "trigger": [{"platform": "time", "at": "s"}],
        "action": [{"service": "input_boolean.turn_off",
                    "target": {"entity_id": "input_boolean.demo_switch"}}],
        "summary": "y",
    })
    usage = MagicMock(input_tokens=12, output_tokens=95,
                      cache_creation_input_tokens=420, cache_read_input_tokens=380)
    fake_resp = MagicMock(content=[tool_block], usage=usage)
    with patch("anthropic.Anthropic") as Anth:
        Anth.return_value.messages.create.return_value = fake_resp
        b = brain.ClaudeBrain(SENSORS)
        b.draft("turn off the boiler")
    assert b.model == "claude-sonnet-4-6"
    assert b.last_usage == {"input_tokens": 12, "output_tokens": 95,
                            "cache_creation_input_tokens": 420, "cache_read_input_tokens": 380}


def test_demobrain_has_no_usage():
    b = DemoBrain(SENSORS)
    assert b.last_usage is None
    assert b.model is None
