from __future__ import annotations

import json
import os
from dataclasses import dataclass

DEFAULT_PRICES_PATH = "prices.json"


def load_prices(path: str | None = None) -> dict:
    path = path or os.environ.get("KOSHR_PRICES") or DEFAULT_PRICES_PATH
    with open(path) as f:
        return json.load(f)


@dataclass
class Cost:
    model: str
    input_tokens: int
    output_tokens: int
    cache_write_tokens: int
    cache_read_tokens: int
    input_cost: float
    output_cost: float
    cache_write_cost: float
    cache_read_cost: float
    total: float
    cache_savings: float


def price(usage: dict, model: str, prices: dict) -> "Cost | None":
    rates = prices.get("models", {}).get(model)
    if rates is None:
        return None
    it = usage.get("input_tokens", 0) or 0
    ot = usage.get("output_tokens", 0) or 0
    cw = usage.get("cache_creation_input_tokens", 0) or 0
    cr = usage.get("cache_read_input_tokens", 0) or 0
    ic = it * rates["input"] / 1e6
    oc = ot * rates["output"] / 1e6
    cwc = cw * rates["cache_write"] / 1e6
    crc = cr * rates["cache_read"] / 1e6
    savings = cr * (rates["input"] - rates["cache_read"]) / 1e6
    return Cost(model, it, ot, cw, cr, ic, oc, cwc, crc, ic + oc + cwc + crc, savings)
