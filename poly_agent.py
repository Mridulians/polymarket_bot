"""
poly_agent.py
─────────────
Polymarket Oracle Lag Bot — Up/Down Edition

Strategy:
  Polymarket now runs "Bitcoin Up or Down - 15 min" markets.
  Binance price feed is FASTER than Polymarket's order book.

  If BTC is clearly trending UP on Binance but Polymarket's
  "Up" share is still priced at $0.50 (50/50), that's free money.

  We buy the "Up" share when Binance momentum is strong.
  We buy the "Down" share when Binance is clearly falling.
"""

import os
import time
import random
import logging
from datetime import datetime, timezone
from dotenv import load_dotenv

import ccxt
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs

from market_scanner import MarketScanner
from hedge_manager import HedgeManager
from risk_manager import RiskManager
from logger_setup import setup_logger

# ──────────────────────────────────────────────
# BOOTSTRAP
# ──────────────────────────────────────────────
load_dotenv()
logger = setup_logger()

HOST         = "https://clob.polymarket.com"
POLY_PK      = os.getenv("POLY_PK")
POLY_ADDRESS = os.getenv("POLY_ADDRESS")
CHAIN_ID     = 137

TARGET_ASSET        = os.getenv("TARGET_ASSET", "BTC/USDT")
TRADE_SIZE          = float(os.getenv("TRADE_SIZE", 0))
HEDGE_ENABLED       = os.getenv("HEDGE_ENABLED", "false").lower() == "true"
MAX_DAILY_TRADES    = int(os.getenv("MAX_DAILY_TRADES", 0))
MAX_POSITION_USDC   = float(os.getenv("MAX_POSITION_USDC", 500))
STOP_LOSS_THRESHOLD = float(os.getenv("STOP_LOSS_THRESHOLD", 0.30))

# Up/Down specific thresholds
# If BTC moved more than this % in last 60s → strong signal
MOMENTUM_THRESHOLD  = float(os.getenv("MOMENTUM_THRESHOLD", 0.15))
# Minimum edge: Up share must be below this to be worth buying
ENTRY_PRICE_MAX     = float(os.getenv("ENTRY_PRICE_MAX", 0.72))


# ──────────────────────────────────────────────
# CLIENT INIT
# ──────────────────────────────────────────────
def init_polymarket_client() -> ClobClient:
    logger.info("Connecting to Polymarket CLOB...")
    client = ClobClient(HOST, key=POLY_PK, chain_id=CHAIN_ID,
                        signature_type=1, funder=POLY_ADDRESS)
    client.set_api_creds(client.create_or_derive_api_creds())
    logger.info("✅ Polymarket client authenticated.")
    return client


def init_binance_client() -> ccxt.binance:
    logger.info("Connecting to Binance...")
    exchange = ccxt.binance({
        "apiKey":  os.getenv("BINANCE_API_KEY"),
        "secret":  os.getenv("BINANCE_SECRET"),
        "options": {"defaultType": "future"},
    })
    logger.info("✅ Binance client ready.")
    return exchange


# ──────────────────────────────────────────────
# PRICE & MOMENTUM
# ──────────────────────────────────────────────
price_history = []  # Stores last N prices for momentum calc

def get_binance_price(exchange: ccxt.binance, symbol: str) -> float:
    ticker = exchange.fetch_ticker(symbol)
    price  = float(ticker["last"])
    # Store with timestamp for momentum
    price_history.append({"ts": time.time(), "price": price})
    # Keep only last 120 seconds of data
    cutoff = time.time() - 120
    while price_history and price_history[0]["ts"] < cutoff:
        price_history.pop(0)
    return price


def get_momentum(lookback_seconds: int = 60) -> float:
    """
    Calculate price momentum over last N seconds.
    Returns % change: positive = going up, negative = going down.
    """
    if len(price_history) < 2:
        return 0.0
    cutoff     = time.time() - lookback_seconds
    old_prices = [p["price"] for p in price_history if p["ts"] >= cutoff]
    if not old_prices:
        return 0.0
    oldest   = old_prices[0]
    newest   = price_history[-1]["price"]
    momentum = ((newest - oldest) / oldest) * 100
    return round(momentum, 4)


def get_poly_price(client: ClobClient, token_id: str) -> float:
    """Get best ask price for a token from Polymarket order book."""
    try:
        ob = client.get_order_book(token_id)
        if not ob.asks:
            return 1.0
        return float(ob.asks[0].price)
    except Exception as e:
        logger.warning(f"Order book fetch failed: {e}")
        return 0.0


# ──────────────────────────────────────────────
# SIGNAL DETECTION
# ──────────────────────────────────────────────
def detect_signal(momentum: float, up_price: float, down_price: float) -> str:
    """
    Determine trade direction based on Binance momentum vs Polymarket prices.

    Returns: "UP", "DOWN", or "NONE"

    The edge: Binance moves fast. If BTC is strongly trending UP
    but Polymarket still shows Up=0.52, Down=0.48 (nearly 50/50),
    the "Up" share is underpriced. Buy it.
    """
    # Strong upward momentum + Up share still cheap
    if momentum > MOMENTUM_THRESHOLD and up_price < ENTRY_PRICE_MAX:
        logger.info(f"📈 UP signal | Momentum={momentum:+.3f}% | Up price={up_price:.3f}")
        return "UP"

    # Strong downward momentum + Down share still cheap
    if momentum < -MOMENTUM_THRESHOLD and down_price < ENTRY_PRICE_MAX:
        logger.info(f"📉 DOWN signal | Momentum={momentum:+.3f}% | Down price={down_price:.3f}")
        return "DOWN"

    return "NONE"


# ──────────────────────────────────────────────
# ORDER EXECUTION
# ──────────────────────────────────────────────
def execute_split_buy(client: ClobClient, token_id: str,
                      total_size: float, limit_price: float) -> list:
    """Split order into randomised chunks to avoid bot detection."""
    if total_size <= 0:
        logger.info("TRADE_SIZE=0 — observation mode, no order placed.")
        return []

    orders  = []
    remaining = total_size
    n_splits  = random.randint(3, 5)
    splits    = []

    for _ in range(n_splits - 1):
        chunk = round(random.uniform(0.15, 0.40) * remaining, 1)
        chunk = max(10.0, chunk)
        splits.append(chunk)
        remaining -= chunk
    splits.append(round(remaining, 1))
    random.shuffle(splits)

    logger.info(f"Splitting {total_size} shares → {splits}")

    for size in splits:
        if size <= 0:
            continue
        try:
            order = client.create_order(
                OrderArgs(price=limit_price, size=size, side="BUY", token_id=token_id)
            )
            orders.append(order)
            logger.info(f"✅ {size} shares @ ${limit_price}")
            time.sleep(random.uniform(1.0, 3.0))
        except Exception as e:
            logger.error(f"Order failed: {e}")

    return orders


# ──────────────────────────────────────────────
# POSITION MONITOR
# ──────────────────────────────────────────────
def monitor_position(client: ClobClient, token_id: str,
                     entry_price: float, expiry_ts: int,
                     hedge_mgr: HedgeManager) -> str:
    """Watch position until expiry or stop-loss."""
    logger.info("📊 Monitoring position...")

    while True:
        now = int(time.time())
        if now >= expiry_ts:
            logger.info("⏰ Market expired — waiting for resolution.")
            return "EXPIRED"

        current = get_poly_price(client, token_id)

        if current < STOP_LOSS_THRESHOLD:
            logger.warning(f"🛑 Stop-loss! Price={current:.3f}")
            if HEDGE_ENABLED:
                hedge_mgr.close_hedge()
            return "LOSS"

        if current >= 0.96:
            logger.info(f"🎯 Near-certain win at {current:.3f} — holding to expiry.")

        pnl_pct = ((current - entry_price) / entry_price) * 100
        logger.info(
            f"Position | Price={current:.3f} | Entry={entry_price:.3f} | "
            f"P&L={pnl_pct:+.1f}% | Expires in {expiry_ts - now}s"
        )
        time.sleep(random.uniform(2.0, 4.0))


# ──────────────────────────────────────────────
# MAIN LOOP
# ──────────────────────────────────────────────
def run_bot():
    logger.info("=" * 60)
    logger.info("🤖 POLYMARKET ORACLE LAG BOT — UP/DOWN EDITION")
    logger.info(f"   Asset: {TARGET_ASSET} | Mode: {'LIVE' if TRADE_SIZE > 0 else 'OBSERVATION'}")
    logger.info(f"   Momentum threshold: {MOMENTUM_THRESHOLD}% | Entry max: {ENTRY_PRICE_MAX}")
    logger.info(f"   Max daily trades: {MAX_DAILY_TRADES} | Max position: ${MAX_POSITION_USDC}")
    logger.info("=" * 60)

    poly_client = init_polymarket_client()
    binance     = init_binance_client()
    scanner     = MarketScanner(poly_client)
    hedge_mgr   = HedgeManager(binance, HEDGE_ENABLED)
    risk_mgr    = RiskManager(MAX_DAILY_TRADES, MAX_POSITION_USDC)

    active_market = None

    while True:
        try:
            # ── A. Find/refresh active market ─────────────────────
            if active_market is None:
                logger.info("🔍 Scanning for best Up/Down market...")
                active_market = scanner.find_best_market(asset="BTC")

                if active_market is None:
                    logger.info("No market found. Retrying in 30s...")
                    time.sleep(30)
                    continue

                logger.info(f"🎯 Targeting: {active_market['description']}")

            # ── B. Fetch prices ────────────────────────────────────
            btc_price  = get_binance_price(binance, TARGET_ASSET)
            momentum   = get_momentum(lookback_seconds=60)
            up_price   = get_poly_price(poly_client, active_market["token_id_up"])
            down_price = get_poly_price(poly_client, active_market["token_id_down"])
            time_left  = active_market["expiry_ts"] - int(time.time())

            logger.info(
                f"BTC=${btc_price:,.2f} | Momentum={momentum:+.3f}% | "
                f"Up={up_price:.3f} Down={down_price:.3f} | "
                f"Expires in {time_left}s"
            )

            # ── C. Check if market expired ─────────────────────────
            if time_left <= 0:
                logger.info("Market expired. Finding next one...")
                active_market = None
                time.sleep(5)
                continue

            # ── D. Detect signal ───────────────────────────────────
            if not risk_mgr.can_trade():
                time.sleep(10)
                continue

            signal = detect_signal(momentum, up_price, down_price)

            # ── E. Execute trade ───────────────────────────────────
            if signal != "NONE" and TRADE_SIZE > 0:
                token_id    = active_market["token_id_up"] if signal == "UP" else active_market["token_id_down"]
                entry_price = up_price if signal == "UP" else down_price
                buy_price   = round(entry_price + 0.01, 3)

                cost = TRADE_SIZE * buy_price
                if cost > MAX_POSITION_USDC:
                    size = int(MAX_POSITION_USDC / buy_price)
                    logger.warning(f"Capping size to {size} shares (${MAX_POSITION_USDC} limit)")
                else:
                    size = TRADE_SIZE

                logger.info(f"🚨 SIGNAL: {signal} | Buying {size} shares @ ${buy_price}")
                placed = execute_split_buy(poly_client, token_id, size, buy_price)

                if placed:
                    risk_mgr.record_trade(size * buy_price)
                    if HEDGE_ENABLED:
                        hedge_mgr.open_short(TARGET_ASSET, float(os.getenv("HEDGE_BTC_SIZE", 0.01)))

                    result = monitor_position(
                        poly_client, token_id, buy_price,
                        active_market["expiry_ts"], hedge_mgr
                    )
                    logger.info(f"📋 Result: {result}")
                    risk_mgr.record_result(result != "LOSS")
                    if HEDGE_ENABLED:
                        hedge_mgr.close_hedge()
                    active_market = None  # Find fresh market next cycle

            elif signal != "NONE" and TRADE_SIZE == 0:
                # Observation mode — log the signal but don't trade
                logger.info(f"👁️  [OBSERVATION] Would have traded: {signal} "
                            f"(set TRADE_SIZE > 0 to go live)")

            time.sleep(random.uniform(1.0, 2.5))

        except KeyboardInterrupt:
            logger.info("⛔ Bot stopped by user.")
            if HEDGE_ENABLED:
                hedge_mgr.close_hedge()
            break
        except Exception as e:
            logger.error(f"Unexpected error: {e}", exc_info=True)
            time.sleep(10)


if __name__ == "__main__":
    run_bot()
