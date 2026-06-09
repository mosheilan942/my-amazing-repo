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
