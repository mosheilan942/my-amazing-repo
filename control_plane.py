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
