#!/usr/bin/env bash
# Publish a few live auctions for local demos, owned by one seller account.
#
#   ./scripts/seed_listings.sh you@example.com
#
# The seller cannot bid on their own auctions, so bid from a second account.
set -euo pipefail

API="${API:-http://localhost:8000}"
EMAIL="${1:?usage: $0 <email of the seller account>}"
read -r -s -p "Password for $EMAIL: " PASSWORD
echo

# Python builds and reads the JSON, so quotes in titles or passwords cannot break it.
field() { python3 -c $'import json, sys\ntry:\n    print(json.load(sys.stdin)[sys.argv[1]])\nexcept Exception:\n    sys.exit(1)' "$1"; }
to_json() { python3 -c 'import json, sys; it = iter(sys.argv[1:]); print(json.dumps(dict(zip(it, it))))' "$@"; }

TOKEN=$(curl -sf -X POST "$API/auth/login" -H 'Content-Type: application/json' \
  -d "$(to_json email "$EMAIL" password "$PASSWORD")" | field access_token) \
  || { echo "login failed: check the email, the password, and that the API is up at $API" >&2; exit 1; }

# A start time just in the past, so publishing makes each auction live at once.
STARTS=$(date -u -d '-1 minute' +%Y-%m-%dT%H:%M:%SZ)

publish() { # title start_price increment minutes_until_close
  local ends id
  ends=$(date -u -d "+$4 minutes" +%Y-%m-%dT%H:%M:%SZ)
  id=$(curl -sf -X POST "$API/listings" -H "Authorization: Bearer $TOKEN" \
    -H 'Content-Type: application/json' \
    -d "$(to_json title "$1" start_price "$2" bid_increment "$3" starts_at "$STARTS" ends_at "$ends")" \
    | field id)
  curl -sf -X POST "$API/listings/$id/publish" -H "Authorization: Bearer $TOKEN" > /dev/null
  printf '  live: %-34s closes in %s min\n' "$1" "$4"
}

publish "Vintage Leica M3 camera" 900.00 25.00 15
publish "Mid-century oak writing desk" 250.00 10.00 45
publish "First-edition sci-fi paperback" 40.00 2.00 90
publish "Hand-thrown stoneware set" 120.00 5.00 180
publish "Restored road bike, 56cm" 400.00 20.00 360
echo "Done. Open http://localhost:5173"