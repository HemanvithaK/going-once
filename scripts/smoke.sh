#!/usr/bin/env bash
# End-to-end check of the split: a listing is published on the api, bids go to bidding,
# the price shows up on the api, a viewer on the realtime service watches it happen, and
# when the auction ends the api records the sale.
#
# Run with the whole stack up:   docker compose up -d --build && ./scripts/smoke.sh
# Run it with your virtualenv active: the viewer check needs the websockets package.
# Takes about a minute, because it waits for a real deadline.
set -uo pipefail

API="${API:-http://localhost:8000}"
BIDDING="${BIDDING:-http://localhost:8001}"
REALTIME="${REALTIME:-ws://localhost:8002}"
HERE="$(cd "$(dirname "$0")" && pwd)"
RUN="$(date +%s)"
PASSWORD="a-long-enough-password"
failures=0

pass() { echo "  PASS  $1"; }
fail() { echo "  FAIL  $1"; failures=$((failures + 1)); }
field() { python3 -c "import sys, json; print(json.load(sys.stdin).get('$1', ''))"; }
same_number() { python3 -c "import sys; from decimal import Decimal; sys.exit(0 if Decimal('$1') == Decimal('$2') else 1)" 2>/dev/null; }

login() {  # prints an access token, registering the user first
  local email="$1"
  curl -s -X POST "$API/auth/register" -H 'content-type: application/json' \
    -d "{\"email\":\"$email\",\"password\":\"$PASSWORD\",\"display_name\":\"${email%%@*}\"}" >/dev/null
  curl -s -X POST "$API/auth/login" -H 'content-type: application/json' \
    -d "{\"email\":\"$email\",\"password\":\"$PASSWORD\"}" | field access_token
}

bid() {  # bid <token> <listing id> <amount> <key>: prints the response body
  curl -s -X POST "$BIDDING/listings/$2/bids" \
    -H "authorization: Bearer $1" -H "Idempotency-Key: $4" -H 'content-type: application/json' \
    -d "{\"max_amount\": \"$3\"}"
}

echo "1. setup"
seller="$(login "smoke-$RUN-seller@example.com")"
alice="$(login "smoke-$RUN-alice@example.com")"
bob="$(login "smoke-$RUN-bob@example.com")"
if [ -z "$seller" ] || [ -z "$alice" ] || [ -z "$bob" ]; then
  fail "could not log in: is the api up at $API?"; exit 1
fi
pass "three users logged in"

starts="$(date -u -d '-60 seconds' +%Y-%m-%dT%H:%M:%SZ)"
ends="$(date -u -d '+45 seconds' +%Y-%m-%dT%H:%M:%SZ)"
listing="$(curl -s -X POST "$API/listings" -H "authorization: Bearer $seller" \
  -H 'content-type: application/json' \
  -d "{\"title\":\"Smoke camera\",\"description\":\"end to end\",\"start_price\":\"100.00\",\"bid_increment\":\"5.00\",\"starts_at\":\"$starts\",\"ends_at\":\"$ends\"}")"
lid="$(echo "$listing" | field id)"
[ -n "$lid" ] || { fail "could not create the listing: $listing"; exit 1; }
curl -s -X POST "$API/listings/$lid/publish" -H "authorization: Bearer $seller" >/dev/null
pass "listing $lid published, ends at $ends"

# A viewer on the realtime service, started before any bid. It keeps retrying until
# bidding knows the listing, then records everything it is sent.
watcher_pid=""
watcher_log="$(mktemp)"
if python3 -c 'import websockets' 2>/dev/null; then
  python3 "$HERE/watch.py" "$lid" --url "$REALTIME" \
    --expect-price 125 --expect-status sold --timeout 170 >"$watcher_log" 2>&1 &
  watcher_pid=$!
fi

echo "2. the api no longer takes bids"
code="$(curl -s -o /dev/null -w '%{http_code}' -X POST "$API/listings/$lid/bids" \
  -H "authorization: Bearer $alice" -H 'content-type: application/json' -d '{"max_amount":"120.00"}')"
[ "$code" = "404" ] && pass "api answers 404 for a bid" || fail "api answered $code for a bid, expected 404"
# The old viewer socket lived on the api. A handshake to an unknown WebSocket route is refused with 403.
code="$(curl -s -m 5 -o /dev/null -w '%{http_code}' \
  -H 'Connection: Upgrade' -H 'Upgrade: websocket' \
  -H 'Sec-WebSocket-Version: 13' -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' \
  "$API/ws/listings/$lid")"
[ "$code" = "403" ] && pass "api has no viewer WebSocket any more" \
  || fail "api answered $code to a WebSocket handshake, expected 403"

echo "3. bids go to bidding"
# The listing reaches bidding through listing-events: relay, then bidding-sync.
accepted=""
for _ in $(seq 1 15); do
  resp="$(bid "$alice" "$lid" "120.00" "smoke-alice-$RUN")"
  if [ -n "$(echo "$resp" | field is_leading)" ]; then accepted=1; break; fi
  sleep 2
done
[ -n "$accepted" ] && pass "alice's bid of 120 accepted" || { fail "bidding never accepted a bid: $resp"; exit 1; }
resp="$(bid "$bob" "$lid" "150.00" "smoke-bob-$RUN")"
[ "$(echo "$resp" | field is_leading)" = "True" ] && pass "bob's 150 leads" || fail "bob is not leading: $resp"
same_number "$(echo "$resp" | field current_price)" 125 && pass "price is 125 (alice's 120 plus the increment)" \
  || fail "price in bidding is not 125: $resp"

echo "4. the price reaches the api"
shown=""
for _ in $(seq 1 15); do
  shown="$(curl -s "$API/listings/$lid" | field current_price)"
  same_number "${shown:-0}" 125 && break
  sleep 2
done
same_number "${shown:-0}" 125 && pass "api shows 125" || fail "api shows '$shown', expected 125"

echo "5. the auction closes and the api records the sale (up to two minutes)"
status=""
for _ in $(seq 1 60); do
  status="$(curl -s "$API/listings/$lid" | field status)"
  [ "$status" = "sold" ] && break
  sleep 2
done
[ "$status" = "sold" ] && pass "api shows the listing as sold" || fail "api status is '$status', expected sold"
orders="$(docker compose exec -T db psql -U auction -d auction -tAc "SELECT count(*) FROM orders WHERE listing_id = '$lid'" 2>/dev/null | tr -d '[:space:]')"
[ "$orders" = "1" ] && pass "exactly one order was written" || fail "orders for this listing: '${orders:-unknown}', expected 1"

echo "6. a viewer on the realtime service saw it happen"
if [ -n "$watcher_pid" ]; then
  if wait "$watcher_pid"; then
    pass "viewer saw price 125, then sold, and versions only went up"
  else
    fail "the viewer did not see what it should have:"
    sed 's/^/          /' "$watcher_log"
  fi
else
  echo "  SKIP  the websockets package is missing: activate your virtualenv and run again"
fi
rm -f "$watcher_log"

echo
if [ "$failures" -eq 0 ]; then echo "ALL CHECKS PASSED"; else echo "$failures CHECK(S) FAILED"; exit 1; fi