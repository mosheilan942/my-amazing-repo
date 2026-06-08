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
