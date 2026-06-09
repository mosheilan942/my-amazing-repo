"""Bring the harness up end-to-end and write harness/tenants.json.

Flow (per the verified spike):
  1. docker compose up -d   (all 4 instances)
  2. wait each HA ready, bootstrap each -> LLAT
  3. set each Pi up as a remote_homeassistant 'remote node' so its discovery
     endpoint exists (the master's link setup calls it)
  4. for each master: render remote-link config with its Pi's host+token,
     docker cp it in, restart the master, wait ready
  5. write tenants.json  (tenant -> Pi url/token + master url/token)
"""
from __future__ import annotations

import json
import os
import subprocess
import time

import requests

from bootstrap import bootstrap

HERE = os.path.dirname(os.path.abspath(__file__))
COMPOSE = ["docker", "compose", "-f", os.path.join(HERE, "docker-compose.yml")]

# tenant_id -> (pi container, pi host-port, master container, master host-port, pi service name)
TENANTS = {
    "tenant-a": ("koshr-pi-a", 18121, "koshr-master-a", 18122, "pi-a"),
    "tenant-b": ("koshr-pi-b", 18123, "koshr-master-b", 18124, "pi-b"),
}


def _wait_ready(port: int, timeout: int = 180) -> None:
    # Probe the frontend root: returns 200 whenever the HTTP server is up, in BOTH
    # pre- and post-onboarding states. (/api/onboarding 404s once onboarding is done.)
    url = f"http://localhost:{port}/"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if requests.get(url, timeout=5).status_code == 200:
                return
        except requests.RequestException:
            pass
        time.sleep(3)
    raise TimeoutError(f"HA on :{port} not ready in {timeout}s")


def _render_master_config(pi_host: str, pi_token: str) -> str:
    tmpl = open(os.path.join(HERE, "config", "master.remote.yaml.tmpl")).read()
    return tmpl.replace("__PI_HOST__", pi_host).replace("__PI_TOKEN__", pi_token)


def _setup_pi_as_remote_node(pi_port: int, pi_token: str) -> None:
    """Register remote_homeassistant's passive 'remote node' entry on the Pi so its
    /api/remote_homeassistant/discovery endpoint exists — the master's link setup calls
    it. In this mode the Pi never dials out (no self-link / OOM). Drives the config-flow
    REST API, then polls discovery to 200 (view registration is async; flow POSTs return
    200 even on abort, so the poll is the real 'slave ready' gate)."""
    base = f"http://localhost:{pi_port}"
    h = {"Authorization": f"Bearer {pi_token}", "Content-Type": "application/json"}
    r = requests.post(f"{base}/api/config/config_entries/flow", headers=h,
                      json={"handler": "remote_homeassistant"}, timeout=30)
    r.raise_for_status()
    flow_id = r.json()["flow_id"]
    r = requests.post(f"{base}/api/config/config_entries/flow/{flow_id}", headers=h,
                      json={"type": "Setup as remote node"}, timeout=30)
    r.raise_for_status()
    result = r.json()
    assert result.get("type") == "create_entry", result
    deadline = time.time() + 60
    while time.time() < deadline:
        if requests.get(f"{base}/api/remote_homeassistant/discovery",
                        headers=h, timeout=5).status_code == 200:
            return
        time.sleep(2)
    raise TimeoutError(f"pi discovery endpoint not ready on :{pi_port}")


def main() -> None:
    # Clean slate every run: bootstrap()'s owner-creation only works on a fresh,
    # un-onboarded instance, so a leftover stack from a prior/partial run would
    # break provisioning mid-flight. Tear down first.
    subprocess.run(COMPOSE + ["down", "-v"], check=False)
    subprocess.run(COMPOSE + ["up", "-d", "--build"], check=True)
    tenants = {}
    for tid, (pi_c, pi_port, master_c, master_port, pi_host) in TENANTS.items():
        _wait_ready(pi_port)
        _wait_ready(master_port)
        pi_token = bootstrap(f"http://localhost:{pi_port}", f"{tid}-pi", "harness-pw")
        _setup_pi_as_remote_node(pi_port, pi_token)
        master_token = bootstrap(f"http://localhost:{master_port}", f"{tid}-master", "harness-pw")

        cfg = _render_master_config(pi_host, pi_token)
        tmp = os.path.join(HERE, f".{master_c}.configuration.yaml")
        open(tmp, "w").write(cfg)
        subprocess.run(["docker", "cp", tmp, f"{master_c}:/config/configuration.yaml"], check=True)
        os.remove(tmp)
        subprocess.run(["docker", "restart", master_c], check=True)
        _wait_ready(master_port)

        tenants[tid] = {
            "tenant_id": tid, "name": tid,
            "ha_url": f"http://localhost:{pi_port}", "token": pi_token,
            "status": "active",
            "master_url": f"http://localhost:{master_port}", "master_token": master_token,
        }
        print(f"provisioned {tid}: pi :{pi_port}, master :{master_port}")

    out = os.path.join(HERE, "tenants.json")
    json.dump(tenants, open(out, "w"), indent=2)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
