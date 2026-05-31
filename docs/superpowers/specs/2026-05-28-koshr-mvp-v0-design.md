# koshr MVP v0 — design

**Date:** 2026-05-28
**Goal:** Prove the end-to-end loop for a demo today: a plain-language command becomes a
**live Home Assistant automation**, visible in HA's UI. Kills the load-bearing unknown
("can we inject an LLM-built routine into our HA?").

## Finish line (demo target)
Plain-language command → automation JSON → `POST` to HA → the automation **appears in the
HA UI** (Settings → Automations) and round-trips on `GET`. Physical device toggle not required.

## Scope
The wedge only: Shabbat/zmanim scheduling set *before* Shabbat. One command, one automation,
local execution. No live on-Shabbat triggers (keeps halachic surface clean).

## Architecture — one Python CLI, four stages
```
command text ─► brain ─► (confirm y/n) ─► commit (REST POST) ─► verify (REST GET) ─► UI shows it
```

### Components
- **`koshr.py`** — CLI entry. Reads command from argv or prompt. Orchestrates the 4 stages.
  `--yes` skips the confirmation prompt (for rehearsal).
- **`brain.py`** — `Brain` protocol returning an HA automation dict
  `{id, alias, trigger, condition, action, mode}` plus a human summary string.
  - **`ClaudeBrain`** (primary) — Anthropic SDK, structured tool-output forced to the automation
    schema. System prompt teaches HA automation shape + the Jewish Calendar sensor IDs. Prompt-cache
    the system prompt. Handles arbitrary commands.
  - **`DemoBrain`** (safety net) — deterministic map for 3 rehearsed commands → valid automations.
    No network/key needed.
  - **Auto-select:** `ClaudeBrain` if `ANTHROPIC_API_KEY` is set, else `DemoBrain`.
- **`ha_client.py`** — thin REST wrapper over `HA_URL` + `HA_TOKEN`:
  - `post_automation(uid, body)` → `POST /api/config/automation/config/<uid>`
  - `get_automation(uid)` → `GET  /api/config/automation/config/<uid>`
  - `list_jewish_calendar_sensors()` → `GET /api/states`, filter `sensor.jewish_calendar*`
  - Header `Authorization: Bearer <HA_TOKEN>`.

### Key decisions (from brainstorm + advisor)
- **`unique_id` = `uuid4().hex`**, used in BOTH the URL path and the body's `id` field.
- **No WebSocket `validate_config` in v0.** A malformed body returns HTTP 400 from the REST POST —
  enough validation for the demo. (Deferred to v0.1.)
- **Ground the canned automations in real sensor IDs:** after HA boots + Jewish Calendar is added,
  fetch actual `sensor.jewish_calendar*` entity IDs so triggers reference real sensors, not guesses.
- **Demo entity:** `input_boolean.demo_switch` so `action` targets something real in HA.
- **Confirm before commit:** print the plain-language summary; require `y` before POSTing.

### Three rehearsed commands (DemoBrain hardcodes; ClaudeBrain handles live)
1. "Turn off the boiler 30 minutes before candle lighting on Friday."
2. "Turn on the hallway lights when Shabbat ends."
3. "Set the AC to 22 degrees an hour before Shabbat starts."

## Infra
- HA in Docker: `ghcr.io/home-assistant/home-assistant:stable`
  (digest `sha256:ceb1202133a5...` — pin a specific version post-demo), `-p 8123:8123`,
  `-v ./ha-config:/config`. `default_config` (includes the `config` integration needed for the
  automation POST endpoint).
- Secrets via `.env`: `HA_URL`, `HA_TOKEN`, `ANTHROPIC_API_KEY`. `.env` git-ignored.
- Add **Jewish Calendar** integration in HA (location set during onboarding).
- Add `input_boolean.demo_switch` helper.

## Verification
- `ha_client.get_automation(uid)` returns the body just POSTed (200, matching `id`).
- Automation is listed in HA UI under Settings → Automations.
- ClaudeBrain path produces a body that POSTs with 200 (not 400).
- DemoBrain path works with `ANTHROPIC_API_KEY` unset.

## Deferred to v0.1+
WebSocket `validate_config`; real WhatsApp/SMS channel; grounding the LLM with the full live
entity registry; zmanim edge cases (multi-day chag, chag adjacent to Shabbat); contract test
pinning HA version + asserting POST shape.

## Risks
- HA automation-config endpoint is functional but not a formally stable public API → pin HA version,
  add contract test (v0.1).
- Live Claude/network on stage → DemoBrain is the always-available fallback.
- Generated automation correctness → plain-language confirmation gate before commit.
