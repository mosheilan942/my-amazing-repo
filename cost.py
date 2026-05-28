from __future__ import annotations

import json
import os

DEFAULT_PRICES_PATH = "prices.json"


def load_prices(path: str | None = None) -> dict:
    path = path or os.environ.get("KOSHR_PRICES") or DEFAULT_PRICES_PATH
    with open(path) as f:
        return json.load(f)
