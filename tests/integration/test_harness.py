import subprocess
import time

import pytest
import requests

from tenant import Tenant, JSONFileStore
from ha_client import HAClient
import control_plane

pytestmark = pytest.mark.integration


def _state(url, token, entity):
    return requests.get(f"{url}/api/states/{entity}",
                        headers={"Authorization": f"Bearer {token}"}, timeout=10).json()["state"]


def _store_from(tenants, tmp_path):
    s = JSONFileStore(str(tmp_path / "tenants.json"))
    for tid, t in tenants.items():
        s.put(Tenant(tid, t["name"], t["ha_url"], t["token"], t["status"]))
    return s


def test_config_push_fires_on_correct_pi(tenants, tmp_path):
    store = _store_from(tenants, tmp_path)
    a = tenants["tenant-a"]
    client = HAClient(a["ha_url"], a["token"])

    # reset demo_switch ON so the boiler automation (turn_off) produces a visible change
    client.call_service("input_boolean", "turn_on", {"entity_id": "input_boolean.demo_switch"})

    # control plane pushes a real DemoBrain automation to tenant-a's Pi
    uid = control_plane.handle(store, "tenant-a", "turn off the boiler before candle lighting")

    # it landed on tenant-a's Pi (normalized read shape: triggers/actions)
    auto = client.get_automation(uid)
    assert auto["id"] == uid
    assert "actions" in auto

    # reload, then poll for THIS push's automation entity by its uid (re-run-safe:
    # the Pi accumulates automations across runs, so don't grab an arbitrary one)
    client.call_service("automation", "reload")
    ent = None
    deadline = time.time() + 15
    while time.time() < deadline:
        states = requests.get(f"{a['ha_url']}/api/states",
                              headers={"Authorization": f"Bearer {a['token']}"}, timeout=10).json()
        ent = next((e["entity_id"] for e in states
                    if e.get("attributes", {}).get("id") == uid), None)
        if ent:
            break
        time.sleep(1)
    assert ent, f"automation {uid} never registered as an entity after reload"
    # force-fire THIS automation (skip_condition bypasses its zmanim trigger)
    client.call_service("automation", "trigger", {"entity_id": ent, "skip_condition": True})

    # the action fired on tenant-a's Pi: demo_switch -> off
    for _ in range(6):
        if _state(a["ha_url"], a["token"], "input_boolean.demo_switch") == "off":
            break
        time.sleep(1)
    assert _state(a["ha_url"], a["token"], "input_boolean.demo_switch") == "off"


def test_push_did_not_leak_to_other_tenant(tenants):
    # tenant-b's Pi must have NO automations from tenant-a's push
    b = tenants["tenant-b"]
    states = requests.get(f"{b['ha_url']}/api/states",
                          headers={"Authorization": f"Bearer {b['token']}"}, timeout=10).json()
    autos = [s["entity_id"] for s in states if s["entity_id"].startswith("automation.")]
    assert autos == [], f"tenant-b leaked automations: {autos}"


def test_network_isolation_master_a_cannot_reach_pi_b(tenants):
    # same network: master-a -> pi-a resolves
    ok = subprocess.run(
        ["docker", "exec", "koshr-master-a", "python", "-c",
         "import socket; socket.gethostbyname('pi-a')"],
        capture_output=True)
    assert ok.returncode == 0, "master-a should reach pi-a on its own network"

    # different network: master-a -> pi-b must NOT resolve
    bad = subprocess.run(
        ["docker", "exec", "koshr-master-a", "python", "-c",
         "import socket; socket.gethostbyname('pi-b')"],
        capture_output=True)
    assert bad.returncode != 0, "ISOLATION BREACH: master-a resolved pi-b"


def test_live_link_mirrors_pi_entities(tenants):
    # EACH master mirrors its OWN Pi's demo_switch (prefixed), proving the per-tenant
    # live link came up for every tenant. Poll briefly — mirroring syncs asynchronously
    # after the master restart.
    for tid, t in tenants.items():
        h = {"Authorization": f"Bearer {t['master_token']}"}
        states, mirrored = [], False
        deadline = time.time() + 15
        while time.time() < deadline:
            states = requests.get(f"{t['master_url']}/api/states", headers=h, timeout=10).json()
            mirrored = any("remote_" in s["entity_id"] and "demo_switch" in s["entity_id"] for s in states)
            if mirrored:
                break
            time.sleep(1)
        assert mirrored, f"{tid}: live link did not mirror the Pi's demo_switch"
        assert len(states) < 100, f"{tid}: entity explosion — include filter regressed ({len(states)})"
