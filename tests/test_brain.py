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
