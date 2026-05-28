"""The brain: plain-language command -> Home Assistant automation body + summary.

Two implementations behind one interface:
  - ClaudeBrain: live Claude, structured tool-output (used when ANTHROPIC_API_KEY is set).
  - DemoBrain:   deterministic map for rehearsed commands (always-available safety net).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class Draft:
    """An automation body (without id) plus a plain-language confirmation."""
    alias: str
    trigger: list[dict]
    action: list[dict]
    summary: str
    condition: list[dict] = field(default_factory=list)
    mode: str = "single"

    def body(self) -> dict:
        return {
            "alias": self.alias,
            "trigger": self.trigger,
            "condition": self.condition,
            "action": self.action,
            "mode": self.mode,
        }


DEMO_SWITCH = "input_boolean.demo_switch"

AUTOMATION_TOOL = {
    "name": "emit_automation",
    "description": "Emit one Home Assistant automation that satisfies the user's request.",
    "input_schema": {
        "type": "object",
        "properties": {
            "alias": {"type": "string", "description": "Short human title for the automation."},
            "trigger": {"type": "array", "items": {"type": "object"}, "description": "HA trigger list."},
            "condition": {"type": "array", "items": {"type": "object"}},
            "action": {"type": "array", "items": {"type": "object"}, "description": "HA action list."},
            "mode": {"type": "string", "enum": ["single", "restart", "queued", "parallel"]},
            "summary": {"type": "string", "description": "One plain sentence the user confirms before it goes live."},
        },
        "required": ["alias", "trigger", "action", "summary"],
    },
}


def _pick(sensors: list[str], *keywords: str, fallback: str) -> str:
    for s in sensors:
        if all(k in s for k in keywords):
            return s
    return fallback


class ClaudeBrain:
    name = "claude"

    def __init__(self, sensors: list[str]):
        import anthropic

        self._client = anthropic.Anthropic()
        self._model = os.environ.get("KOSHR_MODEL", "claude-sonnet-4-6")
        self.model = self._model
        self.last_usage = None
        self._sensors = sensors

    def _system(self) -> list[dict]:
        sensor_lines = "\n".join(f"  - {s}" for s in self._sensors) or "  (none discovered yet)"
        text = (
            "You translate plain-language Shabbat/Yom-Tov home requests into ONE Home Assistant "
            "automation, then call emit_automation. Rules:\n"
            "- This is PRE-Shabbat scheduling only (set before, runs locally). Never invent live "
            "on-Shabbat manual triggers.\n"
            "- Use ONLY entities that exist. The single controllable demo entity is "
            f"'{DEMO_SWITCH}' (an input_boolean) — target it for every action via "
            "input_boolean.turn_on / input_boolean.turn_off.\n"
            "- Zmanim come from these real Jewish Calendar sensors (timestamps):\n"
            f"{sensor_lines}\n"
            "- For time offsets relative to a zman sensor, use a template trigger with "
            "as_timestamp(), e.g. value_template comparing as_timestamp(now()) to "
            "as_timestamp(states('<sensor>')) minus the offset in seconds. Do NOT use timedelta.\n"
            "- Produce valid HA automation JSON (classic 'trigger'/'condition'/'action' keys). "
            "Keep it minimal. Always include a one-sentence 'summary'."
        )
        # cache_control caches the system prompt across calls, but only engages once it
        # exceeds the model's minimum cacheable size (~1024 tokens). Below that, cache_write/
        # read stay 0 and cost.cache_savings is $0 — expected, not a bug.
        return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]

    def draft(self, command: str) -> Draft:
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=self._system(),
            tools=[AUTOMATION_TOOL],
            tool_choice={"type": "tool", "name": "emit_automation"},
            messages=[{"role": "user", "content": command}],
        )
        u = resp.usage
        self.last_usage = {
            "input_tokens": getattr(u, "input_tokens", 0) or 0,
            "output_tokens": getattr(u, "output_tokens", 0) or 0,
            "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
            "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
        }
        tool_use = next((b for b in resp.content if b.type == "tool_use"), None)
        if tool_use is None:
            raise ValueError("Claude did not return an automation (no tool_use block).")
        d = tool_use.input
        return Draft(
            alias=d["alias"],
            trigger=d["trigger"],
            action=d["action"],
            summary=d["summary"],
            condition=d.get("condition", []),
            mode=d.get("mode", "single"),
        )


class DemoBrain:
    name = "demo"
    last_usage = None
    model = None

    def __init__(self, sensors: list[str]):
        candle = _pick(sensors, "candle", fallback="sensor.jewish_calendar_upcoming_shabbat_candle_lighting")
        havdalah = _pick(sensors, "havdalah", fallback="sensor.jewish_calendar_upcoming_shabbat_havdalah")
        self._candle = candle
        self._havdalah = havdalah

    def _before(self, sensor: str, seconds: int) -> list[dict]:
        return [{
            "platform": "template",
            "value_template": (
                f"{{{{ as_timestamp(states('{sensor}')) - {seconds} <= as_timestamp(now()) "
                f"< as_timestamp(states('{sensor}')) }}}}"
            ),
        }]

    def _act(self, service: str) -> list[dict]:
        return [{"service": service, "target": {"entity_id": DEMO_SWITCH}}]

    def draft(self, command: str) -> Draft:
        c = command.lower()
        if "boiler" in c or ("off" in c and "candle" in c):
            return Draft(
                alias="Boiler off 30 min before candle lighting",
                trigger=self._before(self._candle, 1800),
                action=self._act("input_boolean.turn_off"),
                summary="I'll turn the boiler off 30 minutes before candle lighting each Friday.",
            )
        if "light" in c or "shabbat ends" in c or "havdalah" in c:
            return Draft(
                alias="Hallway lights on when Shabbat ends",
                trigger=[{"platform": "time", "at": self._havdalah}],
                action=self._act("input_boolean.turn_on"),
                summary="I'll turn the hallway lights on when Shabbat ends (havdalah).",
            )
        if "ac" in c or "air" in c or "degree" in c or "temperature" in c:
            return Draft(
                alias="AC on one hour before Shabbat",
                trigger=self._before(self._candle, 3600),
                action=self._act("input_boolean.turn_on"),
                summary="I'll switch the AC on one hour before Shabbat starts.",
            )
        raise ValueError(
            "DemoBrain only knows the 3 rehearsed commands (boiler / lights / AC). "
            "Set ANTHROPIC_API_KEY to handle arbitrary commands."
        )


def select(sensors: list[str]):
    if os.environ.get("ANTHROPIC_API_KEY"):
        return ClaudeBrain(sensors)
    return DemoBrain(sensors)
