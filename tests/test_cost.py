import json
import cost


def test_load_prices_from_explicit_path(tmp_path):
    f = tmp_path / "p.json"
    f.write_text(json.dumps({"as_of": "2026-05-28", "models": {"m": {"input": 1}}}))
    p = cost.load_prices(str(f))
    assert p["as_of"] == "2026-05-28"
    assert p["models"]["m"]["input"] == 1


def test_load_prices_env_override(tmp_path, monkeypatch):
    f = tmp_path / "env.json"
    f.write_text(json.dumps({"as_of": "2026-01-01", "models": {}}))
    monkeypatch.setenv("KOSHR_PRICES", str(f))
    assert cost.load_prices()["as_of"] == "2026-01-01"
