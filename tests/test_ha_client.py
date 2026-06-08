from unittest.mock import MagicMock, patch

import pytest

from ha_client import HAClient


def _client(clean_env):
    clean_env.setenv("HA_URL", "http://ha.test:8123/")
    clean_env.setenv("HA_TOKEN", "tok123")
    return HAClient()


def test_requires_env(clean_env):
    with pytest.raises(RuntimeError):
        HAClient()


def test_post_automation_url_headers_body(clean_env):
    c = _client(clean_env)
    with patch("ha_client.requests.post") as post:
        post.return_value = MagicMock(text='{"result":"ok"}', json=lambda: {"result": "ok"})
        post.return_value.raise_for_status = lambda: None
        out = c.post_automation("abc", {"id": "abc", "alias": "x"})
    args, kwargs = post.call_args
    assert args[0] == "http://ha.test:8123/api/config/automation/config/abc"
    assert kwargs["headers"]["Authorization"] == "Bearer tok123"
    assert kwargs["json"] == {"id": "abc", "alias": "x"}
    assert out == {"result": "ok"}


def test_get_automation_url_headers(clean_env):
    c = _client(clean_env)
    with patch("ha_client.requests.get") as get:
        get.return_value = MagicMock(json=lambda: {"id": "abc", "alias": "x"})
        get.return_value.raise_for_status = lambda: None
        out = c.get_automation("abc")
    args, kwargs = get.call_args
    assert args[0] == "http://ha.test:8123/api/config/automation/config/abc"
    assert kwargs["headers"]["Authorization"] == "Bearer tok123"
    assert out == {"id": "abc", "alias": "x"}


def test_jewish_calendar_sensors_filters(clean_env):
    c = _client(clean_env)
    states = [
        {"entity_id": "sensor.jewish_calendar_upcoming_shabbat_candle_lighting"},
        {"entity_id": "sensor.kitchen_temp"},
        {"entity_id": "binary_sensor.jewish_calendar_issur_melacha_in_effect"},
    ]
    with patch("ha_client.requests.get") as get:
        get.return_value = MagicMock(json=lambda: states)
        get.return_value.raise_for_status = lambda: None
        sensors = c.jewish_calendar_sensors()
    assert "sensor.jewish_calendar_upcoming_shabbat_candle_lighting" in sensors
    assert "binary_sensor.jewish_calendar_issur_melacha_in_effect" in sensors
    assert "sensor.kitchen_temp" not in sensors


def test_get_state_url_headers(clean_env):
    c = _client(clean_env)
    with patch("ha_client.requests.get") as get:
        get.return_value = MagicMock(json=lambda: {"entity_id": "input_boolean.demo_switch", "state": "on"})
        get.return_value.raise_for_status = lambda: None
        out = c.get_state("input_boolean.demo_switch")
    args, kwargs = get.call_args
    assert args[0] == "http://ha.test:8123/api/states/input_boolean.demo_switch"
    assert kwargs["headers"]["Authorization"] == "Bearer tok123"
    assert out["state"] == "on"
