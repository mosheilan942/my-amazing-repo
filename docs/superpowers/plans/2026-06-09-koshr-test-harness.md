# koshr Two-Ended Test Harness — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove the full koshr topology locally — N isolated cloud "tenant" HA containers (masters), each linked to its own Raspberry-Pi-simulator HA (slave) — by pushing a Shabbat automation through the control plane and watching it fire on the correct Pi, with cross-tenant isolation asserted.

**Architecture:** Two real Home Assistant instances per tenant in Docker: a *master* container (runs `remote_homeassistant`, dials its Pi, mirrors its entities) and a *Pi-sim* (holds the demo switch, fires automations locally). The koshr control plane pushes automations to the Pi via REST (`POST /api/config/automation/config/<id>` — the verified MVP-v0 loop). Per-tenant Docker networks make A unable to reach B. The control plane is a **thin routing seam** (`control_plane.handle`); heavy hardening (encryption, admin CLI, token rotation) is deferred to its own later plan.

**Tech Stack:** Python 3.14, Docker + Compose, Home Assistant `2026.6.1`, `remote_homeassistant` `4.6`, `requests`, `websockets>=12` (sync client), `pytest`.

---

## Pre-verified ground truth (from the bootstrap spike — do not re-derive)

Every command below was run live against `homeassistant/home-assistant:2026.6.1` during planning. Trust these exact shapes.

- **Image pin:** `homeassistant/home-assistant:2026.6.1`, digest `sha256:59aa8824955c9db491b75d2eebe42bd68494f80c2ec69ec0d66d9dae37d37514`.
- **`remote_homeassistant` pin:** tag `4.6` (no `v` prefix). Tarball: `https://github.com/custom-components/remote_homeassistant/archive/refs/tags/4.6.tar.gz`. `manifest.json` declares `version: 4.6`, `requirements: []`.
- **Headless bootstrap** (4 calls, in order):
  1. `POST /api/onboarding/users` JSON `{"client_id":"<base_url>/","name":..,"username":..,"password":..,"language":"en"}` → `{"auth_code": "<32-char>"}`.
  2. `POST /auth/token` form `grant_type=authorization_code&code=<auth_code>&client_id=<base_url>/` → `{"access_token","refresh_token","expires_in":1800,...}`.
  3. WebSocket `ws://<host>/api/websocket`: recv `auth_required` → send `{"type":"auth","access_token":..}` → recv `auth_ok` → send `{"id":1,"type":"auth/long_lived_access_token","client_name":..,"lifespan":3650}` → recv `{"id":1,"success":true,"result":"<LLAT>"}`.
  4. Verify: `GET /api/` with `Authorization: Bearer <LLAT>` → `{"message":"API running."}`.
- **Automation POST accepts legacy keys** (`trigger`/`condition`/`action`/`service`) and **normalizes on read** to `triggers`/`conditions`/`actions` with `action:` replacing `service:`. Assertions must read the normalized shape.
- **Force-fire** any automation regardless of trigger: `POST /api/services/automation/trigger` JSON `{"entity_id":"automation.<slug>","skip_condition":true}` → runs the action. Used because DemoBrain emits zmanim triggers that can't be flipped on demand.
- **Helper reload without restart:** `POST /api/services/input_boolean/reload`; **automation reload:** `POST /api/services/automation/reload`.
- **GOTCHA — readiness probe:** `/api/onboarding` returns 200 only *before* onboarding; once onboarding completes it returns **404** (verified on an onboarded instance). Do NOT use it to wait for a post-bootstrap restart. Probe the frontend root `/` instead — it returns **200** whenever the HTTP server is up, regardless of onboarding/auth state (also verified).
- **GOTCHA — bind mounts:** a host bind mount under `/tmp` **silently fails** on macOS Docker Desktop (not a shared path); the container gets an empty/independent dir. This harness uses **baked image config + `docker cp`** for dynamic files — no host bind mounts.
- **GOTCHA — self-link OOM:** pointing a master's `remote_homeassistant` at itself (or mirroring un-filtered) recursively re-prefixes entities (`remote_remote_remote_…`), exploded to 23,510 entities and **OOM-killed the container (exit 137)**. Every master config MUST use `include: domains: [input_boolean]` to mirror only real device entities.
- **GOTCHA — `remote_homeassistant` needs the component on BOTH ends.** The master's link setup calls `GET /api/remote_homeassistant/discovery` on the Pi; that endpoint exists only if the **Pi also has the component installed AND loaded as a passive "remote node."** A Pi without it → `EndpointMissing` → no link. (The spike's *self-link* hid this — one instance was both ends.) Fix: bake the component into the Pi image too, and have the provisioner create the Pi's slave entry headlessly via the config-flow API — `POST /api/config/config_entries/flow` (handler `remote_homeassistant`), then submit `{"type": "Setup as remote node"}`. This passive mode registers the discovery view and never dials out (no self-link OOM). Then **poll `/api/remote_homeassistant/discovery` until 200** before connecting the master — view registration is async, and config-flow POSTs return 200 even on abort, so the poll is the unambiguous "slave ready" gate. Verified live: discovery → 200, master then mirrors only the filtered `input_boolean` entities (18 total, no explosion).

---

## File Structure

| File | Create/Modify | Responsibility |
|---|---|---|
| `requirements.txt` | Modify | add `websockets>=12` |
| `.gitignore` | Modify | ignore `harness/tenants.json` |
| `ha_client.py` | Modify | add `get_state(entity_id)` and `call_service(domain, service, data)` |
| `tenant.py` | Create | `Tenant` dataclass, `TenantStore` interface, `JSONFileStore` (thin seam, plaintext token — encryption deferred) |
| `control_plane.py` | Create | `handle(store, tenant_id, command, …)` routing + `UnknownTenant`; the seam the spec commits to |
| `harness/Dockerfile.pi` | Create | HA 2026.6.1 + baked `configuration.yaml` (default_config + `input_boolean` demo_switch/trigger_src) |
| `harness/Dockerfile.master` | Create | HA 2026.6.1 + baked `remote_homeassistant` 4.6 + base `configuration.yaml` |
| `harness/config/pi.configuration.yaml` | Create | Pi-sim config |
| `harness/config/master.base.yaml` | Create | Master base config (no remote block yet) |
| `harness/config/master.remote.yaml.tmpl` | Create | `remote_homeassistant` block template (token + pi-host injected) |
| `harness/docker-compose.yml` | Create | 2 tenants × (master + pi), per-tenant networks, published ports |
| `harness/bootstrap.py` | Create | onboarding → LLAT mint for one instance (reusable) |
| `harness/provision.py` | Create | orchestrate up → bootstrap → inject master configs → write `tenants.json` |
| `harness/teardown.sh` | Create | `docker compose down -v` |
| `harness/README.md` | Create | how to run, pins, gotchas |
| `tests/test_tenant.py` | Create | unit tests for tenant store |
| `tests/test_control_plane.py` | Create | unit tests incl. isolation contract test |
| `tests/test_ha_client.py` | Modify | add tests for `get_state` / `call_service` |
| `tests/test_bootstrap.py` | Create | unit test for bootstrap call sequence (mocked) |
| `tests/integration/test_harness.py` | Create | live stack: config-push fire, isolation, live-link |
| `tests/integration/conftest.py` | Create | `harness_stack` fixture + `integration` marker gate |

**Tenant model:** the control plane pushes automations to the **Pi** (local fire), so a tenant's `ha_url` is its **Pi** URL. Master URLs/tokens live in `tenants.json` only for the live-link demo.

---

### Task 1: Harness scaffolding, deps, gitignore

**Files:**
- Modify: `requirements.txt`
- Modify: `.gitignore`
- Create: `harness/README.md`

- [ ] **Step 1: Add the websockets dependency**

Append to `requirements.txt` (keep existing lines):

```
websockets>=12
```

- [ ] **Step 2: Ignore generated tenant file**

Append to `.gitignore`:

```
harness/tenants.json
```

- [ ] **Step 3: Write the harness README**

Create `harness/README.md`:

````markdown
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
````

- [ ] **Step 4: Commit**

```bash
git add requirements.txt .gitignore harness/README.md
git commit -m "chore: harness scaffolding, websockets dep, gitignore tenants.json"
```

---

### Task 2: `ha_client.get_state`

**Files:**
- Modify: `ha_client.py`
- Test: `tests/test_ha_client.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_ha_client.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ha_client.py::test_get_state_url_headers -v`
Expected: FAIL with `AttributeError: 'HAClient' object has no attribute 'get_state'`

- [ ] **Step 3: Write minimal implementation**

Add to `ha_client.py` (after `get_automation`):

```python
    def get_state(self, entity_id: str) -> dict:
        """Current state object for one entity, e.g. {'state': 'on', 'attributes': {...}}."""
        r = requests.get(
            f"{self.base_url}/api/states/{entity_id}",
            headers=self._headers,
            timeout=15,
        )
        r.raise_for_status()
        return r.json()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_ha_client.py::test_get_state_url_headers -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ha_client.py tests/test_ha_client.py
git commit -m "feat: HAClient.get_state for reading entity state"
```

---

### Task 3: `ha_client.call_service`

**Files:**
- Modify: `ha_client.py`
- Test: `tests/test_ha_client.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_ha_client.py`:

```python
def test_call_service_url_headers_body(clean_env):
    c = _client(clean_env)
    with patch("ha_client.requests.post") as post:
        post.return_value = MagicMock(text="[]", json=lambda: [])
        post.return_value.raise_for_status = lambda: None
        out = c.call_service("automation", "trigger", {"entity_id": "automation.x", "skip_condition": True})
    args, kwargs = post.call_args
    assert args[0] == "http://ha.test:8123/api/services/automation/trigger"
    assert kwargs["headers"]["Authorization"] == "Bearer tok123"
    assert kwargs["json"] == {"entity_id": "automation.x", "skip_condition": True}
    assert out == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ha_client.py::test_call_service_url_headers_body -v`
Expected: FAIL with `AttributeError: 'HAClient' object has no attribute 'call_service'`

- [ ] **Step 3: Write minimal implementation**

Add to `ha_client.py`:

```python
    def call_service(self, domain: str, service: str, data: dict | None = None) -> list:
        """Call a HA service, e.g. call_service('automation','trigger',{'entity_id':..})."""
        r = requests.post(
            f"{self.base_url}/api/services/{domain}/{service}",
            headers=self._headers,
            json=data or {},
            timeout=15,
        )
        r.raise_for_status()
        return r.json() if r.text else []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_ha_client.py::test_call_service_url_headers_body -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ha_client.py tests/test_ha_client.py
git commit -m "feat: HAClient.call_service for firing services"
```

---

### Task 4: `tenant.py` — Tenant + JSONFileStore

**Files:**
- Create: `tenant.py`
- Test: `tests/test_tenant.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_tenant.py`:

```python
import pytest

from tenant import Tenant, JSONFileStore


def test_put_get_roundtrip(tmp_path):
    store = JSONFileStore(str(tmp_path / "tenants.json"))
    store.put(Tenant("tenant-a", "Alpha", "http://pi-a:8123", "tokA"))
    t = store.get("tenant-a")
    assert t.tenant_id == "tenant-a"
    assert t.ha_url == "http://pi-a:8123"
    assert t.token == "tokA"
    assert t.status == "active"


def test_get_unknown_raises_keyerror(tmp_path):
    store = JSONFileStore(str(tmp_path / "tenants.json"))
    with pytest.raises(KeyError):
        store.get("nope")


def test_list_returns_all_ids(tmp_path):
    store = JSONFileStore(str(tmp_path / "tenants.json"))
    store.put(Tenant("tenant-a", "Alpha", "http://pi-a:8123", "tokA"))
    store.put(Tenant("tenant-b", "Bravo", "http://pi-b:8123", "tokB"))
    assert sorted(store.list()) == ["tenant-a", "tenant-b"]


def test_persists_across_instances(tmp_path):
    path = str(tmp_path / "tenants.json")
    JSONFileStore(path).put(Tenant("tenant-a", "Alpha", "http://pi-a:8123", "tokA"))
    assert JSONFileStore(path).get("tenant-a").name == "Alpha"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_tenant.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tenant'`

- [ ] **Step 3: Write minimal implementation**

Create `tenant.py`:

```python
"""Tenant registry — the thin control-plane seam.

NOTE: tokens are stored in plaintext JSON here. At-rest encryption, rotation,
and a managed key are deferred to the control-plane hardening plan; this store
exists to drive the local harness.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass


@dataclass
class Tenant:
    tenant_id: str
    name: str
    ha_url: str
    token: str
    status: str = "active"


class TenantStore:
    """Interface. Implementations: JSONFileStore (now), EncryptedStore (later)."""

    def get(self, tenant_id: str) -> Tenant: ...
    def put(self, tenant: Tenant) -> None: ...
    def list(self) -> list[str]: ...


class JSONFileStore(TenantStore):
    def __init__(self, path: str):
        self._path = path

    def _read(self) -> dict:
        if not os.path.exists(self._path):
            return {}
        with open(self._path) as f:
            return json.load(f)

    def _write(self, data: dict) -> None:
        with open(self._path, "w") as f:
            json.dump(data, f, indent=2)

    def get(self, tenant_id: str) -> Tenant:
        data = self._read()
        if tenant_id not in data:
            raise KeyError(tenant_id)
        return Tenant(**data[tenant_id])

    def put(self, tenant: Tenant) -> None:
        data = self._read()
        data[tenant.tenant_id] = asdict(tenant)
        self._write(data)

    def list(self) -> list[str]:
        return list(self._read().keys())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_tenant.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add tenant.py tests/test_tenant.py
git commit -m "feat: Tenant model + JSONFileStore (thin control-plane seam)"
```

---

### Task 5: `control_plane.handle` + isolation contract test

**Files:**
- Create: `control_plane.py`
- Test: `tests/test_control_plane.py`

- [ ] **Step 1: Write the failing test (incl. the isolation invariant)**

Create `tests/test_control_plane.py`:

```python
import pytest

from brain import DemoBrain
from tenant import Tenant, JSONFileStore
import control_plane


def _store(tmp_path):
    s = JSONFileStore(str(tmp_path / "tenants.json"))
    s.put(Tenant("tenant-a", "Alpha", "http://pi-a:8123", "tokA"))
    s.put(Tenant("tenant-b", "Bravo", "http://pi-b:8123", "tokB"))
    return s


class _FakeClient:
    """Records every (url, token) it is constructed with and every post."""
    log: list = []

    def __init__(self, url, token):
        _FakeClient.log.append(("client", url, token))

    def jewish_calendar_sensors(self):
        return []

    def post_automation(self, uid, body):
        _FakeClient.log.append(("post", uid, body["alias"]))
        return {"result": "ok"}


def test_handle_routes_only_to_this_tenants_ha(tmp_path):
    _FakeClient.log = []
    control_plane.handle(
        _store(tmp_path), "tenant-a", "turn off boiler before candle lighting",
        ha_factory=_FakeClient, select_brain=lambda sensors: DemoBrain(sensors),
    )
    # The client was built for tenant-a ONLY — never B's url or token.
    clients = [e for e in _FakeClient.log if e[0] == "client"]
    assert clients == [("client", "http://pi-a:8123", "tokA")]
    assert all("pi-b" not in str(e) and "tokB" not in str(e) for e in _FakeClient.log)
    # And an automation was pushed.
    assert any(e[0] == "post" for e in _FakeClient.log)


def test_handle_unknown_tenant_raises(tmp_path):
    with pytest.raises(control_plane.UnknownTenant):
        control_plane.handle(
            _store(tmp_path), "ghost", "turn off boiler",
            ha_factory=_FakeClient, select_brain=lambda sensors: DemoBrain(sensors),
        )


def test_handle_refuses_suspended_tenant(tmp_path):
    s = _store(tmp_path)
    s.put(Tenant("tenant-a", "Alpha", "http://pi-a:8123", "tokA", status="suspended"))
    with pytest.raises(ValueError):
        control_plane.handle(
            s, "tenant-a", "turn off boiler",
            ha_factory=_FakeClient, select_brain=lambda sensors: DemoBrain(sensors),
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_control_plane.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'control_plane'`

- [ ] **Step 3: Write minimal implementation**

Create `control_plane.py`:

```python
"""Control plane: route a plain-language command to exactly one tenant's HA.

The isolation invariant: handle() builds a HA client for the addressed tenant
and NO other. The contract test captures every (url, token) the factory sees.
This guards the CONTROL plane only; data-plane isolation (container networks) is
asserted by the integration harness.
"""
from __future__ import annotations

import uuid

from brain import select as default_select
from ha_client import HAClient


class UnknownTenant(KeyError):
    pass


def handle(store, tenant_id, command, *, ha_factory=HAClient, select_brain=default_select) -> str:
    try:
        tenant = store.get(tenant_id)
    except KeyError as e:
        raise UnknownTenant(tenant_id) from e
    if tenant.status != "active":
        raise ValueError(f"tenant {tenant_id!r} is {tenant.status}, refusing to act")
    client = ha_factory(tenant.ha_url, tenant.token)
    sensors = client.jewish_calendar_sensors()
    brain = select_brain(sensors)
    draft = brain.draft(command)
    uid = uuid.uuid4().hex
    client.post_automation(uid, {"id": uid, **draft.body()})
    return uid
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_control_plane.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add control_plane.py tests/test_control_plane.py
git commit -m "feat: control_plane.handle routing + isolation contract test"
```

---

### Task 6: Pi-sim image

**Files:**
- Create: `harness/config/pi.configuration.yaml`
- Create: `harness/Dockerfile.pi`

- [ ] **Step 1: Write the Pi config**

Create `harness/config/pi.configuration.yaml`:

```yaml
default_config:

input_boolean:
  demo_switch:
    name: Demo Switch
  trigger_src:
    name: Trigger Source
```

- [ ] **Step 2: Write the Pi Dockerfile**

Create `harness/Dockerfile.pi`:

```dockerfile
FROM homeassistant/home-assistant:2026.6.1
# The Pi is the link's SLAVE; remote_homeassistant must be installed here too so its
# /api/remote_homeassistant/discovery endpoint exists (the master calls it during link
# setup). Same component bake as Dockerfile.master — the duplication is fine, don't unify.
ADD https://github.com/custom-components/remote_homeassistant/archive/refs/tags/4.6.tar.gz /tmp/rha.tar.gz
RUN tar xzf /tmp/rha.tar.gz -C /tmp \
    && mkdir -p /config/custom_components \
    && cp -r /tmp/remote_homeassistant-4.6/custom_components/remote_homeassistant /config/custom_components/ \
    && rm -rf /tmp/rha.tar.gz /tmp/remote_homeassistant-4.6
COPY config/pi.configuration.yaml /config/configuration.yaml
```

- [ ] **Step 3: Build and verify the config + component are baked**

Run:
```bash
docker build -f harness/Dockerfile.pi -t koshr-pi:dev harness
docker run --rm koshr-pi:dev cat /config/configuration.yaml | grep -c demo_switch
docker run --rm koshr-pi:dev cat /config/custom_components/remote_homeassistant/manifest.json | grep '"version"'
```
Expected: build succeeds; first command prints `1`; second prints a line containing `"version": "4.6"`.

- [ ] **Step 4: Commit**

```bash
git add harness/Dockerfile.pi harness/config/pi.configuration.yaml
git commit -m "feat: koshr-pi image (HA 2026.6.1 + demo switch)"
```

---

### Task 7: Master image (HA + remote_homeassistant 4.6 baked)

**Files:**
- Create: `harness/config/master.base.yaml`
- Create: `harness/Dockerfile.master`

- [ ] **Step 1: Write the master base config**

Create `harness/config/master.base.yaml` (the `remote_homeassistant` block is injected at provision time, once the Pi token exists):

```yaml
default_config:
```

- [ ] **Step 2: Write the master Dockerfile (download + bake the component)**

Create `harness/Dockerfile.master`:

```dockerfile
FROM homeassistant/home-assistant:2026.6.1
ADD https://github.com/custom-components/remote_homeassistant/archive/refs/tags/4.6.tar.gz /tmp/rha.tar.gz
RUN tar xzf /tmp/rha.tar.gz -C /tmp \
    && mkdir -p /config/custom_components \
    && cp -r /tmp/remote_homeassistant-4.6/custom_components/remote_homeassistant /config/custom_components/ \
    && rm -rf /tmp/rha.tar.gz /tmp/remote_homeassistant-4.6
COPY config/master.base.yaml /config/configuration.yaml
```

- [ ] **Step 3: Build and verify the component is baked**

Run:
```bash
docker build -f harness/Dockerfile.master -t koshr-master:dev harness
docker run --rm koshr-master:dev cat /config/custom_components/remote_homeassistant/manifest.json | grep '"version"'
```
Expected: build succeeds; prints a line containing `"version": "4.6"`.

- [ ] **Step 4: Commit**

```bash
git add harness/Dockerfile.master harness/config/master.base.yaml
git commit -m "feat: koshr-master image (HA 2026.6.1 + remote_homeassistant 4.6)"
```

---

### Task 8: `harness/bootstrap.py` — onboarding → LLAT

**Files:**
- Create: `harness/bootstrap.py`
- Test: `tests/test_bootstrap.py`

- [ ] **Step 1: Write the failing test (mock the 4-call sequence)**

Create `tests/test_bootstrap.py`:

```python
import json
from unittest.mock import MagicMock, patch

import harness.bootstrap as bootstrap


class _FakeWS:
    """Scripts the websocket handshake: auth_required -> auth_ok -> token result."""
    def __init__(self):
        self._outbox = [
            json.dumps({"type": "auth_required"}),
            json.dumps({"type": "auth_ok"}),
            json.dumps({"id": 1, "type": "result", "success": True, "result": "LLAT-XYZ"}),
        ]
        self.sent = []

    def __enter__(self): return self
    def __exit__(self, *a): return False
    def recv(self): return self._outbox.pop(0)
    def send(self, msg): self.sent.append(json.loads(msg))


def test_bootstrap_returns_llat():
    post_user = MagicMock(json=lambda: {"auth_code": "CODE123"}, text="{}")
    post_user.raise_for_status = lambda: None
    post_token = MagicMock(json=lambda: {"access_token": "ACCESS123"}, text="{}")
    post_token.raise_for_status = lambda: None
    fake_ws = _FakeWS()

    with patch("harness.bootstrap.requests.post", side_effect=[post_user, post_token]) as post, \
         patch("harness.bootstrap.connect", return_value=fake_ws):
        llat = bootstrap.bootstrap("http://pi-a:8123", "admin", "pw")

    assert llat == "LLAT-XYZ"
    # owner creation hit the onboarding endpoint with client_id
    assert post.call_args_list[0].args[0] == "http://pi-a:8123/api/onboarding/users"
    assert post.call_args_list[0].kwargs["json"]["client_id"] == "http://pi-a:8123/"
    # token exchange used the returned auth_code
    assert post.call_args_list[1].args[0] == "http://pi-a:8123/auth/token"
    assert post.call_args_list[1].kwargs["data"]["code"] == "CODE123"
    # ws: first send is auth with the access token, second mints the LLAT
    assert fake_ws.sent[0] == {"type": "auth", "access_token": "ACCESS123"}
    assert fake_ws.sent[1]["type"] == "auth/long_lived_access_token"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_bootstrap.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'harness.bootstrap'` (and/or missing `harness/__init__.py`).

- [ ] **Step 3: Write minimal implementation**

Create `harness/__init__.py` (empty file).

Create `harness/bootstrap.py`:

```python
"""Headless HA bootstrap: fresh instance -> long-lived access token.

Verified call sequence against HA 2026.6.1:
  POST /api/onboarding/users -> auth_code
  POST /auth/token (authorization_code) -> access_token
  ws /api/websocket: auth -> auth/long_lived_access_token -> LLAT
Only works BEFORE onboarding is completed (owner can be created once).
"""
from __future__ import annotations

import json

import requests
from websockets.sync.client import connect


def bootstrap(base_url: str, username: str, password: str,
              name: str = "Koshr Admin", lifespan_days: int = 3650) -> str:
    base = base_url.rstrip("/")
    client_id = base + "/"

    r = requests.post(f"{base}/api/onboarding/users", json={
        "client_id": client_id, "name": name,
        "username": username, "password": password, "language": "en",
    }, timeout=30)
    r.raise_for_status()
    auth_code = r.json()["auth_code"]

    r = requests.post(f"{base}/auth/token", data={
        "grant_type": "authorization_code", "code": auth_code, "client_id": client_id,
    }, timeout=30)
    r.raise_for_status()
    access_token = r.json()["access_token"]

    ws_url = base.replace("http://", "ws://").replace("https://", "wss://") + "/api/websocket"
    with connect(ws_url) as ws:
        assert json.loads(ws.recv())["type"] == "auth_required"
        ws.send(json.dumps({"type": "auth", "access_token": access_token}))
        assert json.loads(ws.recv())["type"] == "auth_ok"
        ws.send(json.dumps({"id": 1, "type": "auth/long_lived_access_token",
                            "client_name": "koshr-harness", "lifespan": lifespan_days}))
        result = json.loads(ws.recv())
        assert result.get("success"), result
        return result["result"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_bootstrap.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add harness/__init__.py harness/bootstrap.py tests/test_bootstrap.py
git commit -m "feat: headless HA bootstrap (onboarding -> long-lived token)"
```

---

### Task 9: `docker-compose.yml` — 2 tenants, per-tenant networks

**Files:**
- Create: `harness/config/master.remote.yaml.tmpl`
- Create: `harness/docker-compose.yml`

- [ ] **Step 1: Write the remote-link template (with the mandatory include filter)**

Create `harness/config/master.remote.yaml.tmpl`:

```yaml
default_config:

remote_homeassistant:
  instances:
  - host: __PI_HOST__
    port: 8123
    secure: false
    access_token: __PI_TOKEN__
    entity_prefix: "remote_"
    include:
      domains:
      - input_boolean
```

(`__PI_HOST__` / `__PI_TOKEN__` are replaced by `provision.py`. The `include` filter is what prevents the entity-explosion OOM.)

- [ ] **Step 2: Write the compose file**

Create `harness/docker-compose.yml`:

```yaml
name: koshr-harness

services:
  pi-a:
    build: {context: ., dockerfile: Dockerfile.pi}
    image: koshr-pi:dev
    container_name: koshr-pi-a
    ports: ["18121:8123"]
    networks: [net-a]
  master-a:
    build: {context: ., dockerfile: Dockerfile.master}
    image: koshr-master:dev
    container_name: koshr-master-a
    ports: ["18122:8123"]
    networks: [net-a]
  pi-b:
    image: koshr-pi:dev
    container_name: koshr-pi-b
    ports: ["18123:8123"]
    networks: [net-b]
  master-b:
    image: koshr-master:dev
    container_name: koshr-master-b
    ports: ["18124:8123"]
    networks: [net-b]

networks:
  net-a:
  net-b:
```

(`master-a` and `pi-a` share `net-a` so the master can reach `pi-a` by name; `net-b` is separate, so `master-a` cannot reach `pi-b` — the isolation property, asserted in Task 11.)

- [ ] **Step 3: Validate compose + that the two nets are distinct**

Run:
```bash
docker compose -f harness/docker-compose.yml config >/dev/null && echo "compose OK"
docker compose -f harness/docker-compose.yml config | grep -E "net-a|net-b" | sort -u
```
Expected: `compose OK`, and both `net-a` and `net-b` appear.

- [ ] **Step 4: Commit**

```bash
git add harness/docker-compose.yml harness/config/master.remote.yaml.tmpl
git commit -m "feat: harness compose (2 tenants, per-tenant networks)"
```

---

### Task 10: `harness/provision.py` — orchestrate the live stack

**Files:**
- Create: `harness/provision.py`
- Create: `harness/teardown.sh`

- [ ] **Step 1: Write the teardown helper**

Create `harness/teardown.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
docker compose down -v
rm -f tenants.json
echo "harness torn down"
```

Then: `chmod +x harness/teardown.sh`

- [ ] **Step 2: Write the provisioner**

Create `harness/provision.py`:

```python
"""Bring the harness up end-to-end and write harness/tenants.json.

Flow (per the verified spike):
  1. docker compose up -d   (all 4 instances)
  2. wait each HA ready, bootstrap each -> LLAT
  3. for each master: render remote-link config with its Pi's host+token,
     docker cp it in, restart the master, wait ready
  4. write tenants.json  (tenant -> Pi url/token + master url/token)
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
```

- [ ] **Step 3: Run it live and verify the link mirrored Pi entities (filtered, no explosion)**

Run:
```bash
python harness/provision.py
python - <<'PY'
import json, requests
tenants = json.load(open("harness/tenants.json"))
for tid, t in tenants.items():
    pi_state = requests.get(t["ha_url"] + "/api/states/input_boolean.demo_switch",
        headers={"Authorization": "Bearer " + t["token"]}).json()["state"]
    states = requests.get(t["master_url"] + "/api/states",
        headers={"Authorization": "Bearer " + t["master_token"]}).json()
    mirrored = [s["entity_id"] for s in states if "remote_" in s["entity_id"]]
    has_demo = any("demo_switch" in e for e in mirrored)
    print(f"{tid}: pi demo_switch={pi_state} | master entities={len(states)} | demo_switch mirrored={has_demo}")
    assert len(states) < 1000, f"{tid}: entity explosion — include filter missing!"
    assert has_demo, f"{tid}: link did NOT mirror the Pi's demo_switch"
PY
```
Expected: BOTH `tenant-a` and `tenant-b` print `demo_switch=off`, a small `master entities` count (well under 1000), and `demo_switch mirrored=True`.

- [ ] **Step 4: Commit**

```bash
git add harness/provision.py harness/teardown.sh
git commit -m "feat: provision script — up, bootstrap, link, tenants.json"
```

---

### Task 11: Integration tests — config-push fire, isolation, live-link

**Files:**
- Create: `tests/integration/__init__.py` (empty)
- Create: `tests/integration/conftest.py`
- Create: `tests/integration/test_harness.py`

- [ ] **Step 1: Write the integration fixture + marker**

Create `tests/integration/conftest.py`:

```python
import json
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(scope="session")
def tenants():
    path = os.path.join(ROOT, "harness", "tenants.json")
    if not os.path.exists(path):
        pytest.skip("run `python harness/provision.py` first")
    return json.load(open(path))
```

Register the marker — append to `tests/conftest.py`:

```python
def pytest_configure(config):
    config.addinivalue_line("markers", "integration: requires the live docker harness")
```

- [ ] **Step 2: Write the integration tests**

Create `tests/integration/__init__.py` (empty), then `tests/integration/test_harness.py`:

```python
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

    # reload so the new automation entity exists, then force-fire it
    client.call_service("automation", "reload")
    time.sleep(2)
    ent = next(e["entity_id"] for e in requests.get(
        f"{a['ha_url']}/api/states", headers={"Authorization": f"Bearer {a['token']}"}, timeout=10
    ).json() if e["entity_id"].startswith("automation."))
    client.call_service("automation", "trigger", {"entity_id": ent, "skip_condition": True})

    # the action fired on tenant-a's Pi: demo_switch -> off
    for _ in range(6):
        if _state(a["ha_url"], a["token"], "input_boolean.demo_switch") == "off":
            break
        time.sleep(1)
    assert _state(a["ha_url"], a["token"], "input_boolean.demo_switch") == "off"


def test_push_did_not_leak_to_other_tenant(tenants, tmp_path):
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
    # live link came up for every tenant — not just tenant-a.
    for tid, t in tenants.items():
        states = requests.get(f"{t['master_url']}/api/states",
                              headers={"Authorization": f"Bearer {t['master_token']}"}, timeout=10).json()
        assert any("remote_" in s["entity_id"] and "demo_switch" in s["entity_id"] for s in states), \
            f"{tid}: live link did not mirror the Pi's demo_switch"
        assert len(states) < 1000, f"{tid}: entity explosion — include filter regressed"
```

- [ ] **Step 3: Run the integration suite against the live stack**

Run (provision first if not already): `pytest tests/integration -m integration -v`
Expected: 4 PASS — `test_config_push_fires_on_correct_pi`, `test_push_did_not_leak_to_other_tenant`, `test_network_isolation_master_a_cannot_reach_pi_b`, `test_live_link_mirrors_pi_entities`.

- [ ] **Step 4: Commit**

```bash
git add tests/integration tests/conftest.py
git commit -m "test: integration harness — config-push fire, isolation, live-link"
```

---

### Task 12: Full green + docs

**Files:**
- Modify: `harness/README.md` (add results section)

- [ ] **Step 1: Run the full unit suite (no docker needed)**

Run: `pytest -m "not integration" -v`
Expected: all existing + new unit tests PASS (brain, cost, ha_client incl. get_state/call_service, ledger, koshr, tenant, control_plane, bootstrap).

- [ ] **Step 2: Run the full flow end-to-end**

Run:
```bash
python harness/provision.py
pytest tests/integration -m integration -v
harness/teardown.sh
```
Expected: provision prints both tenants; 4 integration tests PASS; teardown removes containers, volumes, and `tenants.json`.

- [ ] **Step 3: Record the proof in the README**

Append to `harness/README.md`:

```markdown
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
```

- [ ] **Step 4: Commit**

```bash
git add harness/README.md
git commit -m "docs: harness results + deferred-scope notes"
```

---

## Self-Review

**Spec coverage:** multi-tenant provisioning (T6,7,9,10) ✓; per-tenant link (T7,9,10, asserted T11) ✓; config-push local fire (T2,3,5,11) ✓; hard isolation — network (T9, asserted T11) + control-plane contract (T5) ✓; thin seam honoring `control_plane.handle` (T5) ✓; deferred scope recorded (T1,T12) ✓; the two-channel model — config-push (T11 fire) + live link (T11 mirror) ✓; known limits stated (README) ✓.

**Placeholder scan:** none — every code/command step is concrete. The only runtime-substituted values are `__PI_HOST__`/`__PI_TOKEN__`, replaced in `provision.py` (Task 10).

**Type/name consistency:** `Tenant(tenant_id, name, ha_url, token, status)` consistent across T4/T5/T11; `JSONFileStore` get/put/list consistent; `control_plane.handle(store, tenant_id, command, *, ha_factory, select_brain)` consistent T5/T11; `bootstrap(base_url, username, password, …)` consistent T8/T10; `HAClient.get_state`/`call_service` defined T2/T3, used T11; image tags `koshr-pi:dev`/`koshr-master:dev` consistent T6/T7/T9; container names `koshr-master-a`/`koshr-pi-b` consistent T9/T10/T11.
