#!/usr/bin/env python3
"""koshr v0 — plain-language command -> live Home Assistant automation.

Usage:
  python koshr.py "turn off the boiler 30 minutes before candle lighting on Friday"
  python koshr.py --yes "turn on the hallway lights when Shabbat ends"
  python koshr.py --demo "..."   # force the deterministic DemoBrain (ignore Claude)
"""
import argparse
import json
import os
import sys
import uuid

import requests
from dotenv import load_dotenv

import brain
import cost
import ledger
from ha_client import HAClient


def report_cost(the_brain, command: str, prices: dict):
    usage = getattr(the_brain, "last_usage", None)
    model = getattr(the_brain, "model", None)
    c = cost.price(usage, model, prices) if (usage and model and prices) else None
    if c:
        print(f"💸 cost: ${c.total:.4f}  (in {c.input_tokens} / out {c.output_tokens} / "
              f"cache-write {c.cache_write_tokens} / cache-read {c.cache_read_tokens} tok; "
              f"saved ${c.cache_savings:.4f} via cache)")
    elif usage and model:
        # An API call happened but we couldn't price it.
        if prices:
            print(f"💸 cost: unknown (no price for {model})")
        else:
            print(f"💸 cost: not computed ({model} ran, but no prices.json)")
    else:
        print("💸 cost: $0.00 (no API call)")
    ledger.record(c, command, the_brain.name)
    return c


def main() -> int:
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("command", nargs="*", help="plain-language request")
    ap.add_argument("--yes", action="store_true", help="skip confirmation (rehearsal)")
    ap.add_argument("--demo", action="store_true", help="force DemoBrain")
    ap.add_argument("--cost-summary", action="store_true", help="print cost summary and exit")
    args = ap.parse_args()

    if args.cost_summary:
        s = ledger.summarize()
        by = ", ".join(f"{k} {v}" for k, v in s["by_brain"].items()) or "none"
        print(f"📊 cost summary ({ledger.DEFAULT_LEDGER_PATH})")
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

    ha = HAClient()
    sensors = ha.jewish_calendar_sensors()
    if not sensors:
        print("⚠️  No Jewish Calendar sensors found — add the integration in HA for real zmanim.\n")

    the_brain = brain.DemoBrain(sensors) if args.demo else brain.select(sensors)
    print(f"🧠 brain: {the_brain.name}\n🗣️  command: {command}\n")

    try:
        draft = the_brain.draft(command)
    except ValueError as e:
        report_cost(the_brain, command, prices)  # API tokens may have been spent
        print(f"❌ {e}")
        return 1

    print(f"📋 {draft.summary}\n")
    print(json.dumps(draft.body(), indent=2, ensure_ascii=False))
    print()

    report_cost(the_brain, command, prices)

    if not args.yes:
        if input("Commit this automation to Home Assistant? [y/N] ").strip().lower() != "y":
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


if __name__ == "__main__":
    sys.exit(main())
