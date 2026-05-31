# Session Memory — Kosher Smart-Home on Home Assistant

> This file is the handoff from a Claude Code web session so work can continue
> **locally**. It is self-contained. Read the "Resume here" section first.

## Resume here (current status)
- **Phase:** concept + architecture locked; the load-bearing technical unknown is
  **verified feasible**. Next is building **MVP v0**.
- **Immediate next action:** build the v0 loop —
  `text command → Claude (structured output → HA automation JSON) → validate →
  POST to a Home Assistant instance → confirm the automation fires`.
- **Two decisions to make before/while coding v0:**
  1. **HA instance for testing** — spin up Home Assistant in Docker locally, or
     point at an existing instance (need its URL + a long-lived access token)?
  2. **v0 input channel** — start with CLI/HTTP (fastest), then swap in
     WhatsApp/SMS later? (Recommended: CLI first.)

## The idea (one paragraph)
A **kosher-friendly natural-language layer on top of Home Assistant (HA)**. An
**LLM** takes plain-language requests over **kosher channels (WhatsApp / SMS /
email / app / web)**, understands intent, and writes the resulting
**automations/config into a local Home Assistant hub** that runs them **offline**.
For the Shabbat-observant (initially Haredi) market. Branded around
**koshr / kosher.com**. (Origin: user's handwritten note, decoded this session.)

## Confirmed decisions
- **"HA" = Home Assistant** — the open-source local automation hub. We **ride on
  it**; we do NOT build the automation engine or device integrations.
- **Not** custom hardware, **not** a new automation platform from scratch.
- **Scope is intentionally open** — we hold both a broad **vision** and a narrow
  **wedge** (see below) and have NOT yet decided which to lead with.
- **Halachic stance:** the wedge (scheduling decided *before* Shabbat) behaves
  exactly like an accepted שעון שבת → **no new psak**. The more we move toward
  live, on-Shabbat triggers, the more halachic surface opens → gate by review.

## Vision vs. Wedge (key strategic axis — undecided)
- **Vision — kosher smart-home:** LLM+HA controls anything (AC, lights, boiler…)
  via plain language, for a community underserved by app-first / cloud-first
  smart-home products.
- **Wedge — smart שעון שבת:** narrowest valuable slice — Shabbat/Yom Tov
  scheduling, set before Shabbat, runs locally. Cleanest MVP, lowest halachic
  risk.

## Architecture (built around Home Assistant)
```
   ┌─ WhatsApp ─┐
   │  SMS       │      ┌──────── CLOUD ────────┐         ┌──── HOME (local) ────┐
   │  email     │────► │  Channel gateways     │         │  Home Assistant hub  │
   │  app / web │      │  LLM brain (Claude)   │ pushes  │  (רכזת) runs          │
   └────────────┘      │  Zmanim + accounts    │ config  │  automations OFFLINE │
        user ◄─────────│  (configuration only) │────────►│   │                  │
      (plain language) └───────────────────────┘         │   ▼ Zigbee/Matter/WiFi│
                                                          │  AC · lights · boiler │
                                                          └──────────────────────┘
```
**Golden rule:** all intelligence runs in the cloud, BEFORE the event; the local
HA hub executes automations on its own and survives loss of internet.

1. **Channels (kosher-friendly):** WhatsApp, SMS, email, app, web → one
   conversation. SMS matters — many kosher phones are feature phones w/o data.
2. **LLM brain (cloud):** Claude reads free-text (Hebrew/Yiddish/English),
   confirms in plain language, emits HA automations/scripts via HA's API.
   Prompt-cache the system prompt.
3. **Zmanim — reuse, don't compute:** HA ships a **"Jewish Calendar" integration**
   (based on the `hdate` lib) exposing candle-lighting/havdalah/parsha/holidays as
   sensors automations can trigger on. Reuse it. *(Verify edge cases:
   multi-day chag, chag adjacent to Shabbat.)*
4. **Local hub = HA (the רכזת):** small box (HA Green/Yellow or a Pi) running HA;
   connects to appliances over Wi-Fi/Zigbee/Z-Wave/Matter; runs automations
   locally → Shabbat-safe, offline-capable. Cloud only pushes config ahead of time.
5. **What we actually build (thin, defensible):** channel ⇄ LLM ⇄ HA
   orchestration; the Shabbat/zmanim domain logic + plain-language UX;
   onboarding/packaging for this market. HA + integrations are reused.

## VERIFIED: the HA loop is real (the unknown is resolved)
The real unknown was *"can we inject an LLM-built routine into our HA?"* — **yes:**
- **`POST /api/config/automation/config/<unique_id>`** — the exact endpoint HA's
  own UI automation editor uses. Writing to it creates/updates a **live**
  automation; HA reloads automations on write.
- **Auth:** long-lived access token → header `Authorization: Bearer <token>`
  (create one at `http://<ha-host>:8123/profile`).
- **Requires** the `config` integration — included in `default_config` (standard
  installs).
- **Caveats:** the endpoint is functional but **not formally documented as a
  stable public API** → pin the HA version + add a contract test. WebSocket config
  commands are **read-only for create** (use REST for writes). Use the WebSocket
  `validate_config` command to sanity-check a generated automation before it goes
  live.
- So the scenario holds: **(1)** user command (any channel) → cloud · **(2)**
  Claude → HA automation JSON · **(3)** POST → live automation.
- Sources: HA Developer Docs — REST API (`developers.home-assistant.io/docs/api/rest/`)
  and WebSocket API (`developers.home-assistant.io/docs/api/websocket/`);
  HA "API" integration (`home-assistant.io/integrations/api/`).

## MVP v0 — kill the unknown end-to-end (the wedge)
Smallest thing that proves the whole loop:
1. **Input** (v0: CLI/HTTP; swap WhatsApp/SMS in later).
2. **Claude** with structured output → a valid **HA automation JSON** (triggers,
   conditions, actions). Confirm in plain language before commit.
3. **`validate_config`** (WebSocket) to check it, then **POST** to
   `/api/config/automation/config/<id>`.
4. **Verify** the automation appears and **fires** (toggle a test light/switch).
Zmanim deferred to HA's **Jewish Calendar integration** as the trigger source
("30 min after candle-lighting"). One appliance, one schedule, offline-safe.

### Suggested v0 build steps (local)
- Stand up HA: `docker run` the `homeassistant/home-assistant` image (or use an
  existing instance); complete onboarding; create a long-lived token; add a test
  light/switch (e.g. a demo/helper entity).
- Add the **Jewish Calendar** integration; set the home location.
- Write a small script/service: command string → Claude (Anthropic SDK,
  structured output) → automation JSON → `validate_config` → POST to the config
  endpoint → read back state / confirm it fires.
- Add a **contract test** that pins the HA version and asserts the POST shape.

## Open questions
1. **Lead with wedge or vision?** (timer-first vs. full smart-home).
2. **Business model:** (a) software/service layer on users' own HA;
   (b) bundled branded HA hub + service; (c) own end product via kosher
   retail/brand. (Page-2 options, undecided.)
3. **Channels at launch:** WhatsApp-only or SMS from day one.
4. **HA Jewish Calendar coverage:** does it handle every zman/edge case, or
   do we supplement it.

## Key risks
1. **Channel reality** — WhatsApp needs data; kosher phones may be SMS-only.
2. **Halachic surface grows with scope** — pure pre-Shabbat scheduling is clean;
   live on-Shabbat triggers are not.
3. **Hub install/onboarding** in filtered-internet homes.
4. **LLM→HA reliability** — generated automations must be correct/safe; needs
   validation + plain-language confirmation before going live.
