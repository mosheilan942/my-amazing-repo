# koshr Multi-Tenant Control Plane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn single-tenant koshr into a multi-tenant control plane where each command routes to exactly one tenant's Home Assistant, with credentials isolated and encrypted at rest.

**Architecture:** A `TenantStore` holds per-tenant `{ha_url, encrypted HA token, status}`. `control_plane.handle(store, tenant_id, command)` resolves one tenant, builds a `HAClient` scoped to *that* tenant only, and runs the existing brain→draft→confirm→post loop. An isolation contract test proves no code path reaches another tenant's HA. `ManualProvisioner` + `admin_cli` manage tenants. The existing CLI keeps working via an env-backed "default" tenant.

**Tech Stack:** Python 3.11+, `cryptography` (Fernet) for at-rest token encryption, `requests`, `pytest`. Existing modules: `brain.py`, `cost.py`, `ledger.py`, `ha_client.py`, `koshr.py`.

**Scope note:** This plan is the **control-plane core** from the spec (`docs/superpowers/specs/2026-05-31-koshr-multi-tenant-control-plane-design.md`). Deferred by design: `ContainerProvisioner`, `/config` volumes, data-plane network isolation, operator portal UI, channel→tenant mapping, KMS. Do **not** implement those here.

---

## File Structure

| File | Responsibility |
|---|---|
| `tenant.py` (new) | `Tenant` dataclass, `TenantStore` interface, `EncryptedFileStore` impl, `UnknownTenant`. |
| `provisioner.py` (new) | `Provisioner` interface + `ManualProvisioner` (register/suspend/destroy over a store). |
| `control_plane.py` (new) | `handle(store, tenant_id, command, …)` orchestration + `report_cost`. |
| `ha_client.py` (modify) | Make constructor pure: require `base_url` + `token`, no env reads. |
| `ledger.py` (modify) | Add `tenant_id` to records; `summarize` gains `tenant_id` filter + `by_tenant`. |
| `koshr.py` (modify) | CLI gains `--tenant`; routes via `control_plane.handle`; env-backed default tenant for back-compat. Drops `report_cost` (moved to control_plane). |
| `admin_cli.py` (new) | Operator commands: `list` / `provision` / `suspend` / `summary`. |
| `tests/…` | One test module per new/changed unit. |

---

## Task 1: Dependency + test-env hygiene

**Files:**
- Modify: `requirements.txt`
- Modify: `tests/conftest.py`
- Modify: `.env.example`

- [ ] **Step 1: Add the crypto dependency**

Append to `requirements.txt` (keep existing lines):

```
cryptography>=42.0
```

- [ ] **Step 2: Install it**

Run: `pip install -r requirements.txt`
Expected: `cryptography` installs without error.

- [ ] **Step 3: Extend `clean_env` to wipe new env vars**

Replace the tuple in `tests/conftest.py`:

```python
import pytest


@pytest.fixture
def clean_env(monkeypatch):
    for var in ("HA_URL", "HA_TOKEN", "ANTHROPIC_API_KEY", "KOSHR_MODEL",
                "KOSHR_MASTER_KEY", "KOSHR_TENANTS", "KOSHR_LEDGER", "KOSHR_PRICES"):
        monkeypatch.delenv(var, raising=False)
    return monkeypatch
```

- [ ] **Step 4: Document the master key in `.env.example`**

Append to `.env.example`:

```
# Multi-tenant control plane. Generate once:
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
KOSHR_MASTER_KEY=
# Where the encrypted tenant registry lives (default: tenants.json)
KOSHR_TENANTS=tenants.json
```

- [ ] **Step 5: Ignore the tenant registry**

Append to `.gitignore`:

```
tenants.json
```

- [ ] **Step 6: Commit**

```bash
git add requirements.txt tests/conftest.py .env.example .gitignore
git commit -m "chore: add cryptography dep + tenant env hygiene"
```

---

## Task 2: Tenant model + encrypted store (put/get)

**Files:**
- Create: `tenant.py`
- Test: `tests/test_tenant.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tenant.py`:

```python
import pytest
from cryptography.fernet import Fernet

import tenant
from tenant import Tenant, EncryptedFileStore, UnknownTenant


@pytest.fixture
def store(tmp_path, clean_env):
    clean_env.setenv("KOSHR_MASTER_KEY", Fernet.generate_key().decode())
    clean_env.setenv("KOSHR_TENANTS", str(tmp_path / "tenants.json"))
    return EncryptedFileStore()


def test_put_then_get_round_trips_and_decrypts_token(store):
    store.put(Tenant("acme", "Acme Home", "http://acme:8123", "active", "a@x.com"), "secret-tok")
    got, token = store.get("acme")
    assert got.tenant_id == "acme"
    assert got.ha_url == "http://acme:8123"
    assert got.status == "active"
    assert token == "secret-tok"


def test_token_is_not_stored_in_plaintext(store, tmp_path):
    store.put(Tenant("acme", "Acme", "http://acme:8123"), "secret-tok")
    raw = (tmp_path / "tenants.json").read_text()
    assert "secret-tok" not in raw
    assert "token_enc" in raw


def test_get_unknown_tenant_raises(store):
    with pytest.raises(UnknownTenant):
        store.get("nope")


def test_missing_master_key_raises(tmp_path, clean_env):
    clean_env.setenv("KOSHR_TENANTS", str(tmp_path / "t.json"))
    s = EncryptedFileStore()
    with pytest.raises(RuntimeError):
        s.put(Tenant("a", "a", "http://a:8123"), "tok")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tenant.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tenant'`.

- [ ] **Step 3: Implement `tenant.py` (model + store core)**

Create `tenant.py`:

```python
"""Tenant registry: per-customer HA endpoint + encrypted token, isolated.

The HA token is the secret. It is encrypted at rest with Fernet (key from
KOSHR_MASTER_KEY) and decrypted only in memory at point of use.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass

from cryptography.fernet import Fernet

DEFAULT_TENANTS_PATH = "tenants.json"


class UnknownTenant(KeyError):
    """Raised when a tenant_id is not in the store."""


@dataclass
class Tenant:
    tenant_id: str
    name: str
    ha_url: str
    status: str = "active"   # "active" | "suspended"
    contact: str = ""


def _fernet() -> Fernet:
    key = os.environ.get("KOSHR_MASTER_KEY")
    if not key:
        raise RuntimeError("KOSHR_MASTER_KEY must be set (Fernet key). Generate with "
                           "Fernet.generate_key().")
    return Fernet(key.encode())


class TenantStore:
    """Interface. Implementations persist tenants and encrypt the HA token at rest."""

    def get(self, tenant_id: str) -> "tuple[Tenant, str]":
        raise NotImplementedError

    def put(self, tenant: Tenant, ha_token: str) -> None:
        raise NotImplementedError

    def list(self) -> "list[Tenant]":
        raise NotImplementedError

    def set_status(self, tenant_id: str, status: str) -> None:
        raise NotImplementedError

    def delete(self, tenant_id: str) -> None:
        raise NotImplementedError


class EncryptedFileStore(TenantStore):
    def __init__(self, path: str | None = None):
        self._path = path or os.environ.get("KOSHR_TENANTS") or DEFAULT_TENANTS_PATH

    def _load(self) -> dict:
        if not os.path.exists(self._path):
            return {}
        with open(self._path) as f:
            return json.load(f)

    def _save(self, data: dict) -> None:
        with open(self._path, "w") as f:
            json.dump(data, f, indent=2)

    def put(self, tenant: Tenant, ha_token: str) -> None:
        data = self._load()
        rec = asdict(tenant)
        rec["token_enc"] = _fernet().encrypt(ha_token.encode()).decode()
        data[tenant.tenant_id] = rec
        self._save(data)

    def get(self, tenant_id: str) -> "tuple[Tenant, str]":
        data = self._load()
        if tenant_id not in data:
            raise UnknownTenant(tenant_id)
        rec = dict(data[tenant_id])
        token = _fernet().decrypt(rec.pop("token_enc").encode()).decode()
        return Tenant(**rec), token
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tenant.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add tenant.py tests/test_tenant.py
git commit -m "feat: Tenant model + encrypted store (put/get)"
```

---

## Task 3: Store list / set_status / delete

**Files:**
- Modify: `tenant.py`
- Test: `tests/test_tenant.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tenant.py`:

```python
def test_list_returns_tenants_without_tokens(store):
    store.put(Tenant("b", "B", "http://b:8123"), "tokb")
    store.put(Tenant("a", "A", "http://a:8123"), "toka")
    listed = store.list()
    assert [t.tenant_id for t in listed] == ["a", "b"]   # sorted
    assert all(not hasattr(t, "token_enc") for t in listed)


def test_set_status_updates_in_place(store):
    store.put(Tenant("a", "A", "http://a:8123"), "toka")
    store.set_status("a", "suspended")
    got, _ = store.get("a")
    assert got.status == "suspended"


def test_set_status_unknown_raises(store):
    with pytest.raises(UnknownTenant):
        store.set_status("nope", "suspended")


def test_delete_removes_tenant(store):
    store.put(Tenant("a", "A", "http://a:8123"), "toka")
    store.delete("a")
    with pytest.raises(UnknownTenant):
        store.get("a")


def test_delete_unknown_raises(store):
    with pytest.raises(UnknownTenant):
        store.delete("nope")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tenant.py -k "list or set_status or delete" -v`
Expected: FAIL — `NotImplementedError` (interface stubs not overridden).

- [ ] **Step 3: Implement the three methods**

Append these methods to `class EncryptedFileStore` in `tenant.py`:

```python
    def list(self) -> "list[Tenant]":
        out = []
        for rec in self._load().values():
            rec = dict(rec)
            rec.pop("token_enc", None)
            out.append(Tenant(**rec))
        return sorted(out, key=lambda t: t.tenant_id)

    def set_status(self, tenant_id: str, status: str) -> None:
        data = self._load()
        if tenant_id not in data:
            raise UnknownTenant(tenant_id)
        data[tenant_id]["status"] = status
        self._save(data)

    def delete(self, tenant_id: str) -> None:
        data = self._load()
        if tenant_id not in data:
            raise UnknownTenant(tenant_id)
        del data[tenant_id]
        self._save(data)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tenant.py -v`
Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add tenant.py tests/test_tenant.py
git commit -m "feat: tenant store list/set_status/delete"
```

---

## Task 4: ManualProvisioner

**Files:**
- Create: `provisioner.py`
- Test: `tests/test_provisioner.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_provisioner.py`:

```python
import pytest
from cryptography.fernet import Fernet

from provisioner import ManualProvisioner
from tenant import EncryptedFileStore, UnknownTenant


@pytest.fixture
def prov(tmp_path, clean_env):
    clean_env.setenv("KOSHR_MASTER_KEY", Fernet.generate_key().decode())
    clean_env.setenv("KOSHR_TENANTS", str(tmp_path / "tenants.json"))
    return ManualProvisioner(EncryptedFileStore())


def test_provision_registers_active_tenant(prov):
    t = prov.provision("acme", "Acme", "http://acme:8123", "tok", contact="a@x.com")
    assert t.status == "active"
    got, token = prov._store.get("acme")
    assert got.name == "Acme"
    assert token == "tok"


def test_suspend_sets_status(prov):
    prov.provision("acme", "Acme", "http://acme:8123", "tok")
    prov.suspend("acme")
    got, _ = prov._store.get("acme")
    assert got.status == "suspended"


def test_destroy_removes_tenant(prov):
    prov.provision("acme", "Acme", "http://acme:8123", "tok")
    prov.destroy("acme")
    with pytest.raises(UnknownTenant):
        prov._store.get("acme")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_provisioner.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'provisioner'`.

- [ ] **Step 3: Implement `provisioner.py`**

Create `provisioner.py`:

```python
"""Provisioning: register / suspend / destroy a tenant's HA.

v1 = ManualProvisioner: register an already-running HA by URL + token. Container
orchestration (ContainerProvisioner) and data-plane network isolation are deferred
per the spec.
"""
from __future__ import annotations

from tenant import Tenant, TenantStore


class Provisioner:
    def provision(self, tenant_id, name, ha_url, ha_token, contact=""):
        raise NotImplementedError

    def suspend(self, tenant_id):
        raise NotImplementedError

    def destroy(self, tenant_id):
        raise NotImplementedError


class ManualProvisioner(Provisioner):
    def __init__(self, store: TenantStore):
        self._store = store

    def provision(self, tenant_id: str, name: str, ha_url: str, ha_token: str,
                  contact: str = "") -> Tenant:
        tenant = Tenant(tenant_id=tenant_id, name=name, ha_url=ha_url,
                        status="active", contact=contact)
        self._store.put(tenant, ha_token)
        return tenant

    def suspend(self, tenant_id: str) -> None:
        self._store.set_status(tenant_id, "suspended")

    def destroy(self, tenant_id: str) -> None:
        self._store.delete(tenant_id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_provisioner.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add provisioner.py tests/test_provisioner.py
git commit -m "feat: ManualProvisioner over tenant store"
```

---

## Task 5: Make `HAClient` pure (no env reads)

**Files:**
- Modify: `ha_client.py:1-17`
- Modify: `tests/test_ha_client.py:8-16`

- [ ] **Step 1: Update the tests to the new pure constructor**

In `tests/test_ha_client.py`, replace the `_client` helper and `test_requires_env`:

```python
def _client():
    return HAClient("http://ha.test:8123/", "tok123")


def test_requires_args():
    with pytest.raises(ValueError):
        HAClient("", "")
```

Then remove the `clean_env` argument from the three remaining test signatures and their `_client(clean_env)` calls so they read `c = _client()`. (The tests no longer touch env.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_ha_client.py -v`
Expected: FAIL — `TypeError`/`RuntimeError` mismatch, because `HAClient` still reads env and raises `RuntimeError`, not `ValueError`.

- [ ] **Step 3: Make the constructor pure**

Replace `ha_client.py` lines 1-17 (imports + `__init__`) with:

```python
"""Thin REST wrapper around the Home Assistant config + states API."""
import requests


class HAClient:
    def __init__(self, base_url: str, token: str):
        if not base_url or not token:
            raise ValueError("HAClient requires base_url and token.")
        self.base_url = base_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
```

(Leave `post_automation`, `get_automation`, `jewish_calendar_sensors` unchanged. The `import os` line is gone.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_ha_client.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add ha_client.py tests/test_ha_client.py
git commit -m "refactor: HAClient takes explicit base_url+token, no env"
```

---

## Task 6: Ledger gains `tenant_id`

**Files:**
- Modify: `ledger.py:14-29` (record), `ledger.py:32-55` (summarize)
- Test: `tests/test_ledger.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ledger.py`:

```python
def test_record_includes_tenant_id(tmp_path):
    p = tmp_path / "led.jsonl"
    ledger.record(_cost(), "a", "claude", tenant_id="acme", path=str(p))
    e = json.loads(p.read_text().splitlines()[0])
    assert e["tenant_id"] == "acme"


def test_summarize_by_tenant_and_filter(tmp_path):
    p = tmp_path / "led.jsonl"
    ledger.record(_cost(), "a", "claude", tenant_id="acme", path=str(p))
    ledger.record(_cost(), "b", "claude", tenant_id="acme", path=str(p))
    ledger.record(_cost(), "c", "claude", tenant_id="beta", path=str(p))
    full = ledger.summarize(str(p))
    assert full["by_tenant"] == {"acme": 2, "beta": 1}
    only_acme = ledger.summarize(str(p), tenant_id="acme")
    assert only_acme["requests"] == 2
    assert only_acme["total_cost"] == pytest.approx(0.0441)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_ledger.py -k "tenant" -v`
Expected: FAIL — `record() got an unexpected keyword argument 'tenant_id'`.

- [ ] **Step 3: Add `tenant_id` to `record`**

In `ledger.py`, change the `record` signature and add the field:

```python
def record(cost_obj, command: str, brain: str, tenant_id: str | None = None,
           path: str | None = None) -> None:
    c = cost_obj
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "tenant_id": tenant_id,
        "brain": brain,
        "command": command,
        "model": c.model if c else None,
        "input_tokens": c.input_tokens if c else 0,
        "output_tokens": c.output_tokens if c else 0,
        "cache_write_tokens": c.cache_write_tokens if c else 0,
        "cache_read_tokens": c.cache_read_tokens if c else 0,
        "total": c.total if c else 0.0,
        "cache_savings": c.cache_savings if c else 0.0,
    }
    with open(_path(path), "a") as f:
        f.write(json.dumps(entry) + "\n")
```

- [ ] **Step 4: Add `tenant_id` filter + `by_tenant` to `summarize`**

Replace `summarize` in `ledger.py`:

```python
def summarize(path: str | None = None, tenant_id: str | None = None) -> dict:
    p = _path(path)
    out = {"requests": 0, "total_cost": 0.0, "avg_cost": 0.0,
           "cache_savings": 0.0, "by_brain": {}, "by_tenant": {}, "skipped": 0}
    if not os.path.exists(p):
        return out
    with open(p) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                out["skipped"] += 1
                continue
            if tenant_id is not None and e.get("tenant_id") != tenant_id:
                continue
            out["requests"] += 1
            out["total_cost"] += e.get("total", 0.0) or 0.0
            out["cache_savings"] += e.get("cache_savings", 0.0) or 0.0
            b = e.get("brain", "?")
            out["by_brain"][b] = out["by_brain"].get(b, 0) + 1
            t = e.get("tenant_id")
            if t is not None:
                out["by_tenant"][t] = out["by_tenant"].get(t, 0) + 1
    if out["requests"]:
        out["avg_cost"] = out["total_cost"] / out["requests"]
    return out
```

- [ ] **Step 5: Run the full ledger suite to verify pass + no regression**

Run: `pytest tests/test_ledger.py -v`
Expected: all passed (old tests still green — `by_tenant` is additive, `tenant_id` defaults to `None`).

- [ ] **Step 6: Commit**

```bash
git add ledger.py tests/test_ledger.py
git commit -m "feat: ledger records tenant_id + per-tenant summary/filter"
```

---

## Task 7: `control_plane.handle` + `report_cost` + isolation contract test

**Files:**
- Create: `control_plane.py`
- Test: `tests/test_control_plane.py`

- [ ] **Step 1: Write the failing tests (isolation + paths)**

Create `tests/test_control_plane.py`:

```python
from unittest.mock import MagicMock

import pytest

import control_plane
from tenant import Tenant, UnknownTenant


@pytest.fixture
def led(tmp_path, monkeypatch):
    monkeypatch.setenv("KOSHR_LEDGER", str(tmp_path / "led.jsonl"))
    return tmp_path / "led.jsonl"


def _store(tenant, token="tok"):
    s = MagicMock()
    s.get.return_value = (tenant, token)
    return s


def _ha_factory(captured):
    def make(url, token):
        captured["url"] = url
        captured["token"] = token
        ha = MagicMock()
        ha.base_url = url
        ha.jewish_calendar_sensors.return_value = []
        ha.get_automation.return_value = {"alias": "Boiler off 30 min before candle lighting"}
        return ha
    return make


def test_handle_routes_only_to_this_tenants_ha(led):
    import brain
    captured = {}
    store = _store(Tenant("acme", "Acme", "http://acme:8123", "active"))
    rc = control_plane.handle(
        store, "acme", "turn off the boiler", assume_yes=True,
        ha_factory=_ha_factory(captured),
        select_brain=lambda s: brain.DemoBrain(s),
    )
    assert captured["url"] == "http://acme:8123"   # never any other tenant's URL
    assert captured["token"] == "tok"
    store.get.assert_called_once_with("acme")
    assert rc == 0


def test_handle_unknown_tenant_refuses(led):
    store = MagicMock()
    store.get.side_effect = UnknownTenant("nope")
    rc = control_plane.handle(store, "nope", "x", assume_yes=True)
    assert rc == 1


def test_handle_suspended_tenant_refuses(led):
    store = _store(Tenant("acme", "Acme", "http://acme:8123", "suspended"))
    rc = control_plane.handle(store, "acme", "x", assume_yes=True,
                              ha_factory=_ha_factory({}))
    assert rc == 1


def test_handle_records_cost_against_tenant(led):
    import brain, json
    store = _store(Tenant("acme", "Acme", "http://acme:8123", "active"))
    control_plane.handle(store, "acme", "turn off the boiler", assume_yes=True,
                         ha_factory=_ha_factory({}),
                         select_brain=lambda s: brain.DemoBrain(s))
    e = json.loads(led.read_text().splitlines()[0])
    assert e["tenant_id"] == "acme"
    assert e["brain"] == "demo"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_control_plane.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'control_plane'`.

- [ ] **Step 3: Implement `control_plane.py`**

Create `control_plane.py`:

```python
"""Tenant-scoped orchestration: one command → exactly one tenant's HA.

handle() is the v0 koshr loop lifted to take a tenant. The only way it reaches an
HA is by constructing an HAClient from the resolved tenant's (url, token) — there
is no global client and no fallback to another tenant. The isolation contract test
in tests/test_control_plane.py enforces this.
"""
from __future__ import annotations

import json
import uuid

import requests
from cryptography.fernet import InvalidToken

import brain as brain_mod
import cost
import ledger
from ha_client import HAClient
from tenant import UnknownTenant


def report_cost(the_brain, command: str, prices, tenant_id: str | None):
    usage = getattr(the_brain, "last_usage", None)
    model = getattr(the_brain, "model", None)
    c = cost.price(usage, model, prices) if (usage and model and prices) else None
    if c:
        print(f"💸 cost: ${c.total:.4f}  (in {c.input_tokens} / out {c.output_tokens} / "
              f"cache-write {c.cache_write_tokens} / cache-read {c.cache_read_tokens} tok; "
              f"saved ${c.cache_savings:.4f} via cache)")
    elif usage and model:
        if prices:
            print(f"💸 cost: unknown (no price for {model})")
        else:
            print(f"💸 cost: not computed ({model} ran, but no prices.json)")
    else:
        print("💸 cost: $0.00 (no API call)")
    ledger.record(c, command, the_brain.name, tenant_id=tenant_id)
    return c


def handle(store, tenant_id: str, command: str, *, assume_yes: bool = False,
           prices=None, ha_factory=HAClient, select_brain=None, confirm=None) -> int:
    try:
        tenant, token = store.get(tenant_id)
    except UnknownTenant:
        print(f"❌ unknown tenant '{tenant_id}'.")
        return 1
    except InvalidToken:
        print(f"❌ could not decrypt credentials for '{tenant_id}' — refusing.")
        return 1

    if tenant.status != "active":
        print(f"❌ tenant '{tenant_id}' is {tenant.status} — refusing.")
        return 1

    ha = ha_factory(tenant.ha_url, token)   # SCOPED to this tenant only
    sensors = ha.jewish_calendar_sensors()
    if not sensors:
        print("⚠️  No Jewish Calendar sensors found — add the integration in HA for real zmanim.\n")

    select = select_brain or brain_mod.select
    the_brain = select(sensors)
    print(f"🧠 brain: {the_brain.name}\n🗣️  command: {command}\n")

    try:
        draft = the_brain.draft(command)
    except ValueError as e:
        report_cost(the_brain, command, prices, tenant_id)  # API tokens may have been spent
        print(f"❌ {e}")
        return 1

    print(f"📋 {draft.summary}\n")
    print(json.dumps(draft.body(), indent=2, ensure_ascii=False))
    print()
    report_cost(the_brain, command, prices, tenant_id)

    if not assume_yes:
        ask = confirm or (lambda: input("Commit this automation to Home Assistant? [y/N] ").strip().lower() == "y")
        if not ask():
            print("Aborted.")
            return 0

    uid = uuid.uuid4().hex
    body = {"id": uid, **draft.body()}
    try:
        ha.post_automation(uid, body)
        got = ha.get_automation(uid)
    except requests.HTTPError as e:
        print(f"❌ HA request failed ({e.response.status_code}): {e.response.text}")
        return 1

    ok = got.get("alias") == draft.alias
    print(f"✅ Automation '{draft.alias}' is live (id {uid}).")
    print(f"   round-trip GET: {'matches' if ok else 'MISMATCH'}")
    print(f"   view it: {ha.base_url}/config/automation/dashboard")
    return 0 if ok else 1
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_control_plane.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add control_plane.py tests/test_control_plane.py
git commit -m "feat: control_plane.handle (tenant-scoped) + isolation contract test"
```

---

## Task 8: CLI `--tenant` + env-backed default tenant; drop `report_cost` from koshr.py

**Files:**
- Modify: `koshr.py`
- Modify: `tests/test_koshr.py`

- [ ] **Step 1: Rewrite `test_koshr.py` against `control_plane.report_cost` + the default store**

Replace `tests/test_koshr.py` entirely:

```python
from unittest.mock import MagicMock

import pytest

import control_plane
import koshr

SONNET = {"as_of": "2026-05-28", "models": {"claude-sonnet-4-6": {
    "input": 3.00, "output": 15.00, "cache_write": 3.75, "cache_read": 0.30}}}


def test_report_cost_prices_and_records(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("KOSHR_LEDGER", str(tmp_path / "led.jsonl"))
    fb = MagicMock()
    fb.name = "claude"
    fb.model = "claude-sonnet-4-6"
    fb.last_usage = {"input_tokens": 1000, "output_tokens": 1000,
                     "cache_creation_input_tokens": 1000, "cache_read_input_tokens": 1000}
    c = control_plane.report_cost(fb, "turn off boiler", SONNET, "acme")
    assert c.total == pytest.approx(0.02205)
    assert "cost:" in capsys.readouterr().out


def test_report_cost_demo_records_zero(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("KOSHR_LEDGER", str(tmp_path / "led.jsonl"))
    fb = MagicMock()
    fb.name = "demo"
    fb.model = None
    fb.last_usage = None
    c = control_plane.report_cost(fb, "lights", SONNET, "acme")
    assert c is None
    assert "no API call" in capsys.readouterr().out


def test_env_default_store_returns_env_tenant(clean_env):
    clean_env.setenv("HA_URL", "http://env:8123")
    clean_env.setenv("HA_TOKEN", "envtok")
    t, token = koshr._EnvDefaultStore().get("default")
    assert t.ha_url == "http://env:8123"
    assert token == "envtok"
    assert t.status == "active"


def test_env_default_store_missing_env_raises(clean_env):
    from tenant import UnknownTenant
    with pytest.raises(UnknownTenant):
        koshr._EnvDefaultStore().get("default")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_koshr.py -v`
Expected: FAIL — `module 'koshr' has no attribute '_EnvDefaultStore'` and `control_plane.report_cost` arity.

- [ ] **Step 3: Rewrite `koshr.py`**

Replace `koshr.py` entirely:

```python
#!/usr/bin/env python3
"""koshr — plain-language command -> live Home Assistant automation (multi-tenant).

Usage:
  # Back-compat single tenant (uses HA_URL/HA_TOKEN from .env as tenant "default"):
  python koshr.py "turn off the boiler 30 minutes before candle lighting on Friday"

  # Multi-tenant (tenant registered via admin_cli, creds from the encrypted store):
  python koshr.py --tenant acme "turn on the hallway lights when Shabbat ends"

  python koshr.py --demo --tenant acme "..."   # force DemoBrain
  python koshr.py --cost-summary               # all tenants
  python koshr.py --cost-summary --tenant acme # one tenant
"""
import argparse
import os
import sys

from dotenv import load_dotenv

import brain
import control_plane
import cost
import ledger
import tenant


class _EnvDefaultStore:
    """Back-compat: expose HA_URL/HA_TOKEN env as the single tenant 'default'."""

    def get(self, tenant_id: str):
        url = os.environ.get("HA_URL")
        token = os.environ.get("HA_TOKEN")
        if not url or not token:
            raise tenant.UnknownTenant(tenant_id)
        return tenant.Tenant("default", "default", url, "active"), token


def main() -> int:
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("command", nargs="*", help="plain-language request")
    ap.add_argument("--tenant", default="default", help="tenant id (default: env-backed 'default')")
    ap.add_argument("--yes", action="store_true", help="skip confirmation (rehearsal)")
    ap.add_argument("--demo", action="store_true", help="force DemoBrain")
    ap.add_argument("--cost-summary", action="store_true", help="print cost summary and exit")
    args = ap.parse_args()

    if args.cost_summary:
        tid = None if args.tenant == "default" else args.tenant
        s = ledger.summarize(tenant_id=tid)
        by = ", ".join(f"{k} {v}" for k, v in s["by_brain"].items()) or "none"
        scope = "all tenants" if tid is None else f"tenant {tid}"
        print(f"📊 cost summary ({ledger.DEFAULT_LEDGER_PATH}) — {scope}")
        print(f"   requests:      {s['requests']}   ({by})")
        print(f"   total spent:   ${s['total_cost']:.4f}")
        print(f"   avg / request: ${s['avg_cost']:.4f}")
        print(f"   cache savings: ${s['cache_savings']:.4f}")
        return 0

    if not args.command:
        ap.error("command is required (or use --cost-summary)")
    command = " ".join(args.command)

    try:
        prices = cost.load_prices()
        if cost.is_stale(prices.get("as_of", "1970-01-01"),
                         int(os.environ.get("KOSHR_PRICE_MAX_AGE_DAYS", "60"))):
            print(f"⚠️  prices last verified {prices['as_of']} "
                  f"({cost.days_old(prices['as_of'])}d ago) — check anthropic.com/pricing")
    except (OSError, ValueError):
        prices = None
        print("⚠️  no prices.json — cost will not be computed (set KOSHR_PRICES).")

    store = _EnvDefaultStore() if args.tenant == "default" else tenant.EncryptedFileStore()
    select_brain = (lambda s: brain.DemoBrain(s)) if args.demo else None

    return control_plane.handle(store, args.tenant, command,
                                assume_yes=args.yes, prices=prices,
                                select_brain=select_brain)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_koshr.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add koshr.py tests/test_koshr.py
git commit -m "feat: koshr CLI --tenant routing + env-backed default tenant"
```

---

## Task 9: Operator `admin_cli.py`

**Files:**
- Create: `admin_cli.py`
- Test: `tests/test_admin_cli.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_admin_cli.py`:

```python
import pytest
from cryptography.fernet import Fernet

import admin_cli


@pytest.fixture
def env(tmp_path, clean_env):
    clean_env.setenv("KOSHR_MASTER_KEY", Fernet.generate_key().decode())
    clean_env.setenv("KOSHR_TENANTS", str(tmp_path / "tenants.json"))
    clean_env.setenv("KOSHR_LEDGER", str(tmp_path / "led.jsonl"))
    return tmp_path


def test_provision_then_list(env, capsys):
    assert admin_cli.main(["provision", "acme", "--name", "Acme",
                           "--ha-url", "http://acme:8123", "--ha-token", "tok"]) == 0
    assert admin_cli.main(["list"]) == 0
    out = capsys.readouterr().out
    assert "acme" in out
    assert "active" in out


def test_suspend_marks_suspended(env, capsys):
    admin_cli.main(["provision", "acme", "--name", "Acme",
                    "--ha-url", "http://acme:8123", "--ha-token", "tok"])
    assert admin_cli.main(["suspend", "acme"]) == 0
    capsys.readouterr()
    admin_cli.main(["list"])
    assert "suspended" in capsys.readouterr().out


def test_summary_all_and_per_tenant(env, capsys):
    import ledger
    p = str(env / "led.jsonl")
    ledger.record(None, "c", "demo", tenant_id="acme", path=p)
    assert admin_cli.main(["summary"]) == 0
    assert "acme" in capsys.readouterr().out
    assert admin_cli.main(["summary", "acme"]) == 0
    assert "tenant acme" in capsys.readouterr().out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_admin_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'admin_cli'`.

- [ ] **Step 3: Implement `admin_cli.py`**

Create `admin_cli.py`:

```python
#!/usr/bin/env python3
"""Operator CLI over the control plane. Stand-in for the future portal.

  python admin_cli.py provision acme --name "Acme" --ha-url http://acme:8123 --ha-token TOK
  python admin_cli.py list
  python admin_cli.py suspend acme
  python admin_cli.py summary [tenant_id]
"""
import argparse
import sys

from dotenv import load_dotenv

import ledger
import tenant
from provisioner import ManualProvisioner


def main(argv=None) -> int:
    load_dotenv()
    ap = argparse.ArgumentParser(prog="admin")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    pp = sub.add_parser("provision")
    pp.add_argument("tenant_id")
    pp.add_argument("--name", required=True)
    pp.add_argument("--ha-url", required=True)
    pp.add_argument("--ha-token", required=True)
    pp.add_argument("--contact", default="")
    sp = sub.add_parser("suspend")
    sp.add_argument("tenant_id")
    mp = sub.add_parser("summary")
    mp.add_argument("tenant_id", nargs="?")
    args = ap.parse_args(argv)

    store = tenant.EncryptedFileStore()
    prov = ManualProvisioner(store)

    if args.cmd == "list":
        for t in store.list():
            print(f"{t.tenant_id:20} {t.status:10} {t.ha_url}  {t.name}")
        return 0
    if args.cmd == "provision":
        t = prov.provision(args.tenant_id, args.name, args.ha_url, args.ha_token, args.contact)
        print(f"✅ provisioned {t.tenant_id} ({t.name}) → {t.ha_url}")
        return 0
    if args.cmd == "suspend":
        prov.suspend(args.tenant_id)
        print(f"⏸  suspended {args.tenant_id}")
        return 0
    if args.cmd == "summary":
        s = ledger.summarize(tenant_id=args.tenant_id)
        scope = f"tenant {args.tenant_id}" if args.tenant_id else "all tenants"
        print(f"📊 {scope}: {s['requests']} req, ${s['total_cost']:.4f} total, "
              f"${s['cache_savings']:.4f} saved")
        if not args.tenant_id:
            for tid, n in s["by_tenant"].items():
                print(f"   {tid}: {n} req")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_admin_cli.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add admin_cli.py tests/test_admin_cli.py
git commit -m "feat: admin_cli (list/provision/suspend/summary)"
```

---

## Task 10: Full-suite green + back-compat sanity

**Files:** none (verification only)

- [ ] **Step 1: Run the whole suite**

Run: `pytest -v`
Expected: every test passes (Tasks 2-9 plus the pre-existing `test_brain.py`, `test_cost.py`). If any pre-existing test fails, fix the cause before continuing — do not edit a test to hide a regression.

- [ ] **Step 2: Back-compat smoke (no real HA needed)**

Run: `python koshr.py --cost-summary`
Expected: prints the "all tenants" summary block, exit 0 — proves the CLI still runs with no `--tenant`.

- [ ] **Step 3: Multi-tenant smoke (in-memory, no real HA)**

Run:

```bash
export KOSHR_MASTER_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
export KOSHR_TENANTS=/tmp/koshr_tenants.json
python admin_cli.py provision demo1 --name "Demo One" --ha-url http://localhost:8123 --ha-token FAKE
python admin_cli.py list
```

Expected: `provision` prints ✅, `list` shows `demo1  active  http://localhost:8123  Demo One`. (No real HA call is made by provision/list.) Clean up: `rm -f /tmp/koshr_tenants.json`.

- [ ] **Step 4: Final commit (if any verification fix was needed)**

```bash
git add -A
git commit -m "test: full multi-tenant suite green + back-compat verified"
```

---

## Self-Review Notes (author checklist — already applied)

- **Spec coverage:** TenantStore+encryption (T2-3), ManualProvisioner (T4), pure HAClient (T5), ledger tenant_id (T6), control_plane.handle + isolation contract test (T7), CLI --tenant + back-compat (T8), admin_cli (T9). Deferred items (ContainerProvisioner, volumes, network isolation, portal, channel, KMS) are intentionally absent.
- **Isolation invariant:** enforced by `test_handle_routes_only_to_this_tenants_ha` — capturing the URL/token passed to the HA factory and asserting they are this tenant's, plus `store.get` called once with this tenant_id.
- **Type consistency:** `Tenant(tenant_id,name,ha_url,status,contact)`, `store.get → (Tenant, token)`, `report_cost(brain,command,prices,tenant_id)`, `ledger.record(...,tenant_id=...)`, `summarize(path,tenant_id)` are used identically across all tasks.
- **Scope reminder for the implementer:** the suspend≠stop-execution gap and data-plane network isolation are KNOWN and DEFERRED (see spec). Do not try to "fix" them here.
