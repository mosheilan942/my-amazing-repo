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
