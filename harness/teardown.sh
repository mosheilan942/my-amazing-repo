#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
docker compose down -v
rm -f tenants.json
echo "harness torn down"
