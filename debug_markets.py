"""
debug_markets.py
────────────────
Tests the deterministic slug approach to find BTC Up/Down markets.
Run this to confirm before running the main bot.
"""

import time
import requests

now = int(time.time())

def round_down(ts, interval):
    return ts - (ts % interval)

# Calculate slugs
ts_15m = round_down(now, 900)
ts_5m  = round_down(now, 300)

slugs = [
    f"btc-updown-15m-{ts_15m}",
    f"btc-updown-15m-{ts_15m + 900}",
    f"btc-updown-15m-{ts_15m - 900}",
    f"btc-updown-5m-{ts_5m}",
    f"btc-updown-5m-{ts_5m + 300}",
    f"eth-updown-15m-{ts_15m}",
]

print(f"Current Unix time: {now}")
print(f"Testing {len(slugs)} slugs...\n")
print("=" * 60)

for slug in slugs:
    resp = requests.get(
        "https://gamma-api.polymarket.com/events",
        params={"slug": slug},
        timeout=10
    )
    data   = resp.json()
    events = data if isinstance(data, list) else data.get("events", [])

    if events:
        e = events[0]
        markets   = e.get("markets", [])
        token_ids = []
        for m in markets:
            clob = m.get("clobTokenIds", [])
            if isinstance(clob, str):
                import json
                try: clob = json.loads(clob)
                except: pass
            if isinstance(clob, list):
                token_ids.extend(clob)

        print(f"✅ FOUND: {slug}")
        print(f"   Title     : {e.get('title', 'N/A')}")
        print(f"   Liquidity : ${float(e.get('liquidity') or 0):,.2f}")
        print(f"   Token IDs : {token_ids[:2]}")
    else:
        print(f"❌ Not found: {slug}")
    print()

print("=" * 60)
print("If all show ❌, check polymarket.com/crypto/15M to confirm markets are currently live.")