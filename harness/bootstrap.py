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
