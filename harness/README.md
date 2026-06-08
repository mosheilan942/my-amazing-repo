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
