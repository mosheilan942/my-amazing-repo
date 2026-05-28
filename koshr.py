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
from ha_client import HAClient


def main() -> int:
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("command", nargs="+", help="plain-language request")
    ap.add_argument("--yes", action="store_true", help="skip confirmation (rehearsal)")
    ap.add_argument("--demo", action="store_true", help="force DemoBrain")
    args = ap.parse_args()
    command = " ".join(args.command)

    ha = HAClient()
    sensors = ha.jewish_calendar_sensors()
    if not sensors:
        print("⚠️  No Jewish Calendar sensors found — add the integration in HA for real zmanim.\n")

    the_brain = brain.DemoBrain(sensors) if args.demo else brain.select(sensors)
    print(f"🧠 brain: {the_brain.name}\n🗣️  command: {command}\n")

    try:
        draft = the_brain.draft(command)
    except ValueError as e:
        print(f"❌ {e}")
        return 1

    print(f"📋 {draft.summary}\n")
    print(json.dumps(draft.body(), indent=2, ensure_ascii=False))
    print()

    if not args.yes:
        if input("Commit this automation to Home Assistant? [y/N] ").strip().lower() != "y":
            print("Aborted.")
            return 0

    uid = uuid.uuid4().hex
    body = {"id": uid, **draft.body()}
    try:
        ha.post_automation(uid, body)
    except requests.HTTPError as e:
        print(f"❌ POST failed ({e.response.status_code}): {e.response.text}")
        return 1

    got = ha.get_automation(uid)
    ok = got.get("alias") == draft.alias
    print(f"✅ Automation '{draft.alias}' is live (id {uid}).")
    print(f"   round-trip GET: {'matches' if ok else 'MISMATCH'}")
    print(f"   view it: {ha.base_url}/config/automation/dashboard")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
