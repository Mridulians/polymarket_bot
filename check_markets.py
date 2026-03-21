import requests

url = "https://gamma-api.polymarket.com/markets"
params = {
    "active": "true",
    "closed": "false",
    "limit": 100,
    "tag_slug": "crypto"
}

resp = requests.get(url, params=params, timeout=15)
markets = resp.json()

print(f"Total crypto markets: {len(markets)}\n")
print("=" * 60)

for m in markets:
    q = m.get("question", "")
    end = m.get("endDate", "N/A")
    liquidity = float(m.get("liquidity", 0))
    volume = float(m.get("volume", 0))
    
    # Show ALL markets, not just BTC
    print(f"📌 {q[:70]}")
    print(f"   Ends: {end[:10]} | Liquidity: ${liquidity:,.0f} | Volume: ${volume:,.0f}")
    print()