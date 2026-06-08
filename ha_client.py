"""Thin REST wrapper around the Home Assistant config + states API."""
import os

import requests


class HAClient:
    def __init__(self, base_url: str | None = None, token: str | None = None):
        base = base_url or os.environ.get("HA_URL")
        token = token or os.environ.get("HA_TOKEN")
        if not base or not token:
            raise RuntimeError("HA_URL and HA_TOKEN must be set (see .env.example).")
        self.base_url = base.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def post_automation(self, uid: str, body: dict) -> dict:
        """Create/update a live automation. Bad bodies return HTTP 400."""
        r = requests.post(
            f"{self.base_url}/api/config/automation/config/{uid}",
            headers=self._headers,
            json=body,
            timeout=15,
        )
        r.raise_for_status()
        return r.json() if r.text else {}

    def get_automation(self, uid: str) -> dict:
        r = requests.get(
            f"{self.base_url}/api/config/automation/config/{uid}",
            headers=self._headers,
            timeout=15,
        )
        r.raise_for_status()
        return r.json()

    def get_state(self, entity_id: str) -> dict:
        """Current state object for one entity, e.g. {'state': 'on', 'attributes': {...}}."""
        r = requests.get(
            f"{self.base_url}/api/states/{entity_id}",
            headers=self._headers,
            timeout=15,
        )
        r.raise_for_status()
        return r.json()

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

    def jewish_calendar_sensors(self) -> list[str]:
        """Real entity IDs of the Jewish Calendar integration, so triggers aren't guesses."""
        r = requests.get(f"{self.base_url}/api/states", headers=self._headers, timeout=15)
        r.raise_for_status()
        return sorted(
            s["entity_id"]
            for s in r.json()
            if s["entity_id"].startswith(("sensor.jewish_calendar", "binary_sensor.jewish_calendar"))
        )
