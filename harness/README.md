# koshr test harness

Proves the topology: N isolated tenant HA "masters" in the cloud, each linked to
its own Raspberry-Pi-simulator HA, with a Shabbat automation pushed through the
control plane and fired on the correct Pi. Isolation between tenants is asserted.

## Pins (verified)
- Home Assistant: `homeassistant/home-assistant:2026.6.1`
  (`sha256:59aa8824955c9db491b75d2eebe42bd68494f80c2ec69ec0d66d9dae37d37514`)
- `remote_homeassistant`: tag `4.6`

## Run
```bash
pip install -r requirements.txt
python harness/provision.py            # builds, starts, bootstraps, writes harness/tenants.json
pytest tests/integration -m integration -v
harness/teardown.sh                    # docker compose down -v
```

## Hard-won gotchas
- No host bind mounts under /tmp (silently fails on macOS Docker Desktop). We bake
  config into images and use `docker cp` for dynamic files.
- A master's `remote_homeassistant` MUST filter `include: domains: [input_boolean]`.
  Un-filtered / self-links recursively re-prefix entities and OOM-kill the container.
- DemoBrain emits zmanim triggers; force them with the `automation.trigger` service
  (`skip_condition: true`).

## Proven (Task 12)
- 2 isolated tenants, each master↔Pi linked via remote_homeassistant 4.6.
- control_plane.handle pushed a Shabbat automation to the correct Pi; it fired
  there (demo_switch toggled) and did NOT appear on the other tenant's Pi.
- master-a cannot resolve pi-b (per-tenant Docker networks) — data-plane isolation.
- Live link mirrored only the Pi's input_boolean entities (filter holds, no OOM).

## Not proven here (deferred)
- Real NAT traversal / WireGuard tunnel (single-host docker reaches freely).
- Token encryption at rest, rotation, admin CLI (control-plane hardening plan).
- Channels (email/web/app/WhatsApp) — harness drives control_plane.handle directly.
- Plain-language confirmation before commit — `control_plane.handle` posts directly
  (fine for the automated harness); the confirm-before-go-live step belongs to the
  deferred channel/LLM layer and will need a draft/commit split in the seam.
- Zman-triggered firing — the harness force-fires automations (`skip_condition`); it does
  NOT install the Jewish Calendar integration, so a real zman trigger firing the
  automation is not exercised here.
