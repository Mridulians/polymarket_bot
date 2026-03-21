# 🤖 Polymarket Oracle Lag Arbitrage Bot

A fully automated Python trading bot that exploits the **latency gap** between Binance's real-time BTC price feed and Polymarket's prediction market order book.

---

## 📁 File Structure

```
polymarket_bot/
├── poly_agent.py        ← Main bot loop (run this)
├── market_scanner.py    ← Auto-discovers expiring BTC markets
├── hedge_manager.py     ← Delta-neutral hedge via Binance Perps
├── risk_manager.py      ← Daily limits, circuit breakers
├── logger_setup.py      ← Console + rotating file logging
├── requirements.txt     ← Python dependencies
├── .env.example         ← Copy to .env and fill in your keys
└── logs/
    └── bot.log          ← Created automatically at runtime
```

---

## 🚀 Quick Start

### Step 1 — Install Dependencies
```bash
pip install -r requirements.txt
```

### Step 2 — Set Up Your .env File
```bash
cp .env.example .env
# Open .env in a text editor and fill in your keys
```

Required keys:
| Key | Where to get it |
|-----|----------------|
| `POLY_PK` | Your Polygon wallet **private key** (e.g. MetaMask → export) |
| `POLY_ADDRESS` | Your Polygon wallet public address |
| `BINANCE_API_KEY` | Binance → Account → API Management |
| `BINANCE_SECRET` | Same as above |

### Step 3 — Fund Your Wallet
- Send **USDC** to your Polygon address (used to buy YES shares)
- Polymarket requires USDC on the **Polygon network**
- Binance account needs **USDT** in Futures wallet for hedging

### Step 4 — Run the Bot
```bash
python poly_agent.py
```

---

## 🧠 Strategy Explained

```
[9:58 PM]  BTC on Binance: $96,200  (above $95k strike ✅)
           Polymarket YES share: $0.62  (should be ~$0.99 ❌)

           ↑ This gap = FREE MONEY for whoever acts first

[Bot]      Buys 300 YES shares @ $0.62 = $186 spent
           Shorts 0.01 BTC on Binance Perps (hedge)

[10:00 PM] Market resolves: BTC was above $95k ✅
           YES shares pay out $1.00 each
           Profit: $300 - $186 = $114 (61% return in 2 min)
           Binance hedge: tiny loss (BTC barely moved)
           Net profit: ~$110
```

### Why Does the Gap Exist?
- Binance price updates in **milliseconds**
- Polymarket's order book updates in **seconds** (Polygon blockchain latency)
- Human traders are slow to respond; bots like this capture the spread

---

## ⚙️ Configuration

All settings are in your `.env` file:

| Setting | Default | Description |
|---------|---------|-------------|
| `STRIKE_PRICE` | 95000 | BTC price threshold to watch |
| `TRADE_SIZE` | 100 | YES shares to buy per trade |
| `PROFIT_THRESHOLD` | 0.05 | Minimum 5 cent spread to trigger |
| `PRICE_GAP_TRIGGER` | 50 | BTC must be $50+ above strike |
| `HEDGE_ENABLED` | true | Enable Binance futures hedge |
| `MAX_DAILY_TRADES` | 20 | Max trades before bot stops for the day |
| `MAX_POSITION_USDC` | 500 | Max USDC per trade |
| `STOP_LOSS_THRESHOLD` | 0.30 | Exit if price falls below this |

---

## 🛡️ Risk Management

The bot has **three layers** of protection:

1. **Position Sizing** — Never deploys more than `MAX_POSITION_USDC` per trade
2. **Daily Limits** — Hard cap of `MAX_DAILY_TRADES` per day
3. **Circuit Breaker** — Bot pauses after 3 consecutive losses
4. **Stop-Loss** — Auto-exits position if Polymarket price collapses
5. **Delta Hedge** — Binance short offsets directional BTC risk

---

## ⚠️ Risks & Limitations

| Risk | Reality |
|------|---------|
| **HFT Competition** | Professional firms with co-located servers do this at millisecond speed. Your edge depends on finding markets they've overlooked. |
| **Thin Liquidity** | Small 15-min markets may not have enough shares to fill large orders |
| **Slippage** | Fast BTC moves can eat your spread; the hedge limits this |
| **Rate Limits** | Polymarket bans aggressive bots; the random delays help |
| **Geo-restrictions** | Polymarket is **blocked in the USA**. Ensure compliance in your jurisdiction. |

---

## 🔧 Advanced Tweaks

**Run faster (WebSocket mode)**
Replace the polling loop with WebSocket subscriptions:
```python
# py_clob_client supports websockets for real-time order book
from py_clob_client.websocket import PolymarketWebSocket
```

**Multiple strikes simultaneously**
Modify `market_scanner.py` to track multiple strike prices in parallel using `asyncio`.

**Better stealth**
Use residential proxies and rotate them every few requests to avoid IP-based rate limiting.

---

## 📋 Example Log Output

```
2026-01-03 21:55:01  INFO      🤖 POLYMARKET ORACLE LAG BOT — STARTING UP
2026-01-03 21:55:01  INFO         Target: BTC/USDT | Strike: $95,000
2026-01-03 21:55:02  INFO      ✅ Polymarket client authenticated.
2026-01-03 21:55:02  INFO      🔍 Scanning for best expiring market...
2026-01-03 21:55:03  INFO      🎯 Target: Will BTC be above $95,000 at 10:00 PM?
2026-01-03 21:55:03  INFO         Expires in 4m 57s | Liquidity: $12,400
2026-01-03 21:58:12  INFO      BTC=$96,218 | Strike=$95,000 | Gap=$1,218 | PolyPrice=0.612
2026-01-03 21:58:12  INFO      🚨 ARBITRAGE SIGNAL DETECTED!
2026-01-03 21:58:12  INFO         Splitting 100 shares into 4 orders: [22.0, 31.0, 18.0, 29.0]
2026-01-03 21:58:13  INFO      ✅ Order placed: 22 shares @ $0.622
2026-01-03 21:58:15  INFO      ✅ Order placed: 31 shares @ $0.622
2026-01-03 22:00:01  INFO      📋 Trade result: WIN
```

---

## ❗ Disclaimer

This bot is provided for **educational purposes only**.  
Cryptocurrency trading involves substantial risk of loss.  
Past performance does not guarantee future results.  
Always start with small amounts and test thoroughly before deploying real capital.
