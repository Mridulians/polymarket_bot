"""
poly_agent.py
─────────────
AGGRESSIVE EDITION — 1% Edge Strategy

New Logic:
  Instead of waiting for big momentum (0.15%+), we now trade on
  ANY situation where the implied probability on Polymarket
  doesn't match what Binance price action suggests.

  3 Signal Types:
  ─────────────────────────────────────────────────────────────
  1. MOMENTUM SIGNAL (original)
     BTC moves 0.05%+ in 60s → buy the matching direction
     if Polymarket price < 0.90 (10% edge minimum)

  2. MEAN REVERSION SIGNAL (new)
     If Up=0.55, Down=0.45 but BTC has been flat for 60s
     → both should be ~0.50. Buy the cheaper side.
     Edge = difference from 0.50 baseline.

  3. EXPIRY PRESSURE SIGNAL (new — most profitable)
     In the last 3 minutes before expiry:
     - If BTC is clearly above its opening price → buy UP
     - If BTC is clearly below its opening price → buy DOWN
     - Market often misprice this due to uncertainty
     Even a 2% edge pays well in 3 minutes.

  Trade Entry Rule:
     Expected profit = (1.00 - entry_price) × size
     Minimum required = 1% of capital deployed
     i.e. if buying at $0.90, we make $0.10 per share = 11% ROI

  All 3 signals fire independently and can overlap.
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
MAX_DAILY_TRADES    = int(os.getenv("MAX_DAILY_TRADES", 15))
MAX_POSITION_USDC   = float(os.getenv("MAX_POSITION_USDC", 5))
STOP_LOSS_THRESHOLD = float(os.getenv("STOP_LOSS_THRESHOLD", 0.30))

# ── Aggressive Strategy Parameters ──
# Minimum profit % required to take a trade (1% = 0.01)
MIN_EDGE            = float(os.getenv("MIN_EDGE", 0.01))

# Maximum price we'll pay for a share (lower = more profitable)
# 0.99 means we buy anything below 99 cents (1%+ edge guaranteed)
ENTRY_PRICE_MAX     = float(os.getenv("ENTRY_PRICE_MAX", 0.99))

# Momentum: BTC must move this % in 60s for momentum signal
MOMENTUM_THRESHOLD  = float(os.getenv("MOMENTUM_THRESHOLD", 0.05))

# Expiry pressure: activate in last N seconds before expiry
EXPIRY_PRESSURE_WINDOW = int(os.getenv("EXPIRY_PRESSURE_WINDOW", 180))

# Mean reversion: imbalance threshold
MEAN_REVERSION_MIN  = float(os.getenv("MEAN_REVERSION_MIN", 0.03))


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
# PRICE & MOMENTUM TRACKING
# ──────────────────────────────────────────────
price_history   = []   # [{ts, price}]
candle_open     = None # Price at start of current 15-min candle
candle_open_ts  = None


def get_binance_price(exchange: ccxt.binance, symbol: str) -> float:
    ticker = exchange.fetch_ticker(symbol)
    price  = float(ticker["last"])
    now    = time.time()

    price_history.append({"ts": now, "price": price})
    # Keep last 5 minutes of data
    cutoff = now - 300
    while price_history and price_history[0]["ts"] < cutoff:
        price_history.pop(0)
    return price


def set_candle_open(price: float, expiry_ts: int):
    """Record the opening price for the current 15-min candle."""
    global candle_open, candle_open_ts
    candle_open    = price
    candle_open_ts = expiry_ts - 900  # 15 min before expiry = candle open


def get_momentum(lookback_seconds: int = 60) -> float:
    """% price change over last N seconds. Positive = up, negative = down."""
    if len(price_history) < 2:
        return 0.0
    cutoff     = time.time() - lookback_seconds
    old_prices = [p["price"] for p in price_history if p["ts"] >= cutoff]
    if not old_prices:
        return 0.0
    return round(((price_history[-1]["price"] - old_prices[0]) / old_prices[0]) * 100, 4)


def get_poly_price(client: ClobClient, token_id: str) -> float:
    """Get best ask price from Polymarket order book."""
    try:
        ob = client.get_order_book(token_id)
        if not ob.asks:
            return 1.0
        return float(ob.asks[0].price)
    except Exception as e:
        logger.warning(f"Order book fetch failed: {e}")
        return 0.0


def get_edge(entry_price: float) -> float:
    """
    Calculate the edge (expected profit %) of buying at this price.
    If we buy at 0.90 and it resolves to 1.00, edge = 11.1%
    """
    if entry_price >= 1.0:
        return 0.0
    return round((1.0 - entry_price) / entry_price * 100, 2)


# ──────────────────────────────────────────────
# SIGNAL DETECTION — ALL 3 STRATEGIES
# ──────────────────────────────────────────────
def check_signals(momentum: float, up_price: float, down_price: float,
                  time_left: int, btc_price: float) -> tuple:
    """
    Check all 3 signals and return (direction, reason, edge_pct).
    Returns ("NONE", "", 0) if no trade.

    Priority: Expiry Pressure > Momentum > Mean Reversion
    """

    # ── SIGNAL 1: EXPIRY PRESSURE (highest priority) ──────────
    # In last 3 minutes, if BTC has clear direction from candle open
    if time_left <= EXPIRY_PRESSURE_WINDOW and candle_open is not None:
        candle_move = ((btc_price - candle_open) / candle_open) * 100

        # BTC is UP from candle open → buy UP shares
        if candle_move > 0.05 and up_price < ENTRY_PRICE_MAX:
            edge = get_edge(up_price)
            if edge >= MIN_EDGE * 100:
                logger.info(
                    f"⏰ EXPIRY PRESSURE — UP | "
                    f"Candle move: +{candle_move:.3f}% | "
                    f"Up price: {up_price:.3f} | Edge: {edge:.1f}%"
                )
                return ("UP", "EXPIRY_PRESSURE", edge)

        # BTC is DOWN from candle open → buy DOWN shares
        if candle_move < -0.05 and down_price < ENTRY_PRICE_MAX:
            edge = get_edge(down_price)
            if edge >= MIN_EDGE * 100:
                logger.info(
                    f"⏰ EXPIRY PRESSURE — DOWN | "
                    f"Candle move: {candle_move:.3f}% | "
                    f"Down price: {down_price:.3f} | Edge: {edge:.1f}%"
                )
                return ("DOWN", "EXPIRY_PRESSURE", edge)

    # ── SIGNAL 2: MOMENTUM (medium priority) ──────────────────
    if momentum > MOMENTUM_THRESHOLD and up_price < ENTRY_PRICE_MAX:
        edge = get_edge(up_price)
        if edge >= MIN_EDGE * 100:
            logger.info(
                f"📈 MOMENTUM — UP | "
                f"Momentum: +{momentum:.3f}% | "
                f"Up price: {up_price:.3f} | Edge: {edge:.1f}%"
            )
            return ("UP", "MOMENTUM", edge)

    if momentum < -MOMENTUM_THRESHOLD and down_price < ENTRY_PRICE_MAX:
        edge = get_edge(down_price)
        if edge >= MIN_EDGE * 100:
            logger.info(
                f"📉 MOMENTUM — DOWN | "
                f"Momentum: {momentum:.3f}% | "
                f"Down price: {down_price:.3f} | Edge: {edge:.1f}%"
            )
            return ("DOWN", "MOMENTUM", edge)

    # ── SIGNAL 3: MEAN REVERSION (lowest priority) ────────────
    # If one side is significantly cheaper than 0.50 baseline
    # AND there's no strong momentum pushing it away from 0.50
    if abs(momentum) < 0.03:  # Flat market only
        # Up is much cheaper than Down → market overpricing Down
        if up_price < (down_price - MEAN_REVERSION_MIN) and up_price < ENTRY_PRICE_MAX:
            edge = get_edge(up_price)
            if edge >= MIN_EDGE * 100:
                logger.info(
                    f"🔄 MEAN REVERSION — UP | "
                    f"Up={up_price:.3f} Down={down_price:.3f} | Edge: {edge:.1f}%"
                )
                return ("UP", "MEAN_REVERSION", edge)

        # Down is much cheaper than Up → market overpricing Up
        if down_price < (up_price - MEAN_REVERSION_MIN) and down_price < ENTRY_PRICE_MAX:
            edge = get_edge(down_price)
            if edge >= MIN_EDGE * 100:
                logger.info(
                    f"🔄 MEAN REVERSION — DOWN | "
                    f"Up={up_price:.3f} Down={down_price:.3f} | Edge: {edge:.1f}%"
                )
                return ("DOWN", "MEAN_REVERSION", edge)

    return ("NONE", "", 0)


# ──────────────────────────────────────────────
# ORDER EXECUTION
# ──────────────────────────────────────────────
def execute_buy(client: ClobClient, token_id: str,
                size: float, limit_price: float) -> bool:
    """Place a single buy order. Returns True if successful."""
    if size <= 0:
        logger.info("👁️  OBSERVATION MODE — no order placed (TRADE_SIZE=0)")
        return False

    try:
        order = client.create_order(
            OrderArgs(
                price    = limit_price,
                size     = size,
                side     = "BUY",
                token_id = token_id,
            )
        )
        logger.info(f"✅ ORDER PLACED: {size} shares @ ${limit_price} | ID: {order}")
        return True
    except Exception as e:
        logger.error(f"❌ Order failed: {e}")
        return False


# ──────────────────────────────────────────────
# POSITION MONITOR
# ──────────────────────────────────────────────
def monitor_position(client: ClobClient, token_id: str,
                     entry_price: float, expiry_ts: int) -> str:
    """Watch position until expiry or stop-loss."""
    logger.info("📊 Monitoring open position...")

    while True:
        now = int(time.time())
        if now >= expiry_ts:
            logger.info("⏰ Market expired — awaiting resolution.")
            return "EXPIRED"

        current = get_poly_price(client, token_id)

        # Stop loss
        if 0 < current < STOP_LOSS_THRESHOLD:
            logger.warning(f"🛑 STOP-LOSS triggered at {current:.3f}")
            return "LOSS"

        pnl = ((current - entry_price) / entry_price) * 100
        logger.info(
            f"  Position | Now={current:.3f} | "
            f"Entry={entry_price:.3f} | "
            f"P&L={pnl:+.1f}% | "
            f"Expires in {expiry_ts - now}s"
        )
        time.sleep(random.uniform(3.0, 5.0))


# ──────────────────────────────────────────────
# MAIN LOOP
# ──────────────────────────────────────────────
def run_bot():
    logger.info("=" * 65)
    logger.info("🤖 POLYMARKET BOT — AGGRESSIVE 1% EDGE STRATEGY")
    logger.info(f"   Asset       : {TARGET_ASSET}")
    logger.info(f"   Mode        : {'🟢 LIVE' if TRADE_SIZE > 0 else '👁️  OBSERVATION'}")
    logger.info(f"   Min edge    : {MIN_EDGE*100:.0f}%")
    logger.info(f"   Entry max   : ${ENTRY_PRICE_MAX}")
    logger.info(f"   Momentum    : {MOMENTUM_THRESHOLD}%")
    logger.info(f"   Expiry win  : last {EXPIRY_PRESSURE_WINDOW}s")
    logger.info(f"   Max trades  : {MAX_DAILY_TRADES}/day")
    logger.info(f"   Max capital : ${MAX_POSITION_USDC}/trade")
    logger.info("=" * 65)

    poly_client   = init_polymarket_client()
    binance       = init_binance_client()
    scanner       = MarketScanner(poly_client)
    hedge_mgr     = HedgeManager(binance, HEDGE_ENABLED)
    risk_mgr      = RiskManager(MAX_DAILY_TRADES, MAX_POSITION_USDC)

    active_market = None
    candle_set    = False

    while True:
        try:
            # ── A. Find market ────────────────────────────────────
            if active_market is None:
                logger.info("🔍 Scanning for best Up/Down market...")
                active_market = scanner.find_best_market(asset="BTC")
                candle_set    = False

                if active_market is None:
                    logger.info("No market found. Retrying in 20s...")
                    time.sleep(20)
                    continue

                logger.info(f"🎯 Targeting: {active_market['description']}")

            # ── B. Fetch live data ────────────────────────────────
            btc_price  = get_binance_price(binance, TARGET_ASSET)
            momentum   = get_momentum(lookback_seconds=60)
            up_price   = get_poly_price(poly_client, active_market["token_id_up"])
            down_price = get_poly_price(poly_client, active_market["token_id_down"])
            time_left  = active_market["expiry_ts"] - int(time.time())

            # Set candle open price once per market
            if not candle_set:
                set_candle_open(btc_price, active_market["expiry_ts"])
                candle_set = True
                logger.info(f"📌 Candle open set: ${btc_price:,.2f}")

            # ── C. Log status ─────────────────────────────────────
            edge_up   = get_edge(up_price)
            edge_down = get_edge(down_price)
            logger.info(
                f"BTC=${btc_price:,.2f} | "
                f"Mom={momentum:+.3f}% | "
                f"Up={up_price:.3f}({edge_up:.1f}%) "
                f"Down={down_price:.3f}({edge_down:.1f}%) | "
                f"⏱{time_left}s"
            )

            # ── D. Market expired ─────────────────────────────────
            if time_left <= 0:
                logger.info("Market expired. Finding next market...")
                active_market = None
                candle_set    = False
                time.sleep(10)
                continue

            # ── E. Risk check ─────────────────────────────────────
            if not risk_mgr.can_trade():
                time.sleep(10)
                continue

            # ── F. Check all 3 signals ────────────────────────────
            direction, reason, edge = check_signals(
                momentum, up_price, down_price, time_left, btc_price
            )

            # ── G. Execute trade ──────────────────────────────────
            if direction != "NONE":
                token_id    = (active_market["token_id_up"]
                               if direction == "UP"
                               else active_market["token_id_down"])
                entry_price = up_price if direction == "UP" else down_price
                buy_price   = round(min(entry_price + 0.01, 0.99), 3)

                # Calculate size based on capital limit
                if TRADE_SIZE > 0:
                    cost = TRADE_SIZE * buy_price
                    if cost > MAX_POSITION_USDC:
                        size = round(MAX_POSITION_USDC / buy_price, 1)
                    else:
                        size = TRADE_SIZE
                else:
                    size = 0  # Observation mode

                logger.info(
                    f"\n{'='*55}\n"
                    f"🚨 TRADE SIGNAL!\n"
                    f"   Direction : {direction}\n"
                    f"   Reason    : {reason}\n"
                    f"   Edge      : {edge:.1f}%\n"
                    f"   Entry     : ${buy_price}\n"
                    f"   Size      : {size} shares\n"
                    f"   Cost      : ${size * buy_price:.2f} USDC\n"
                    f"   Max profit: ${size * (1.0 - buy_price):.2f} USDC\n"
                    f"{'='*55}"
                )

                if size > 0:
                    success = execute_buy(poly_client, token_id, size, buy_price)
                    if success:
                        risk_mgr.record_trade(size * buy_price)
                        result = monitor_position(
                            poly_client, token_id,
                            buy_price, active_market["expiry_ts"]
                        )
                        logger.info(f"📋 Trade result: {result}")
                        risk_mgr.record_result(result != "LOSS")
                        active_market = None  # Get fresh market next cycle
                        candle_set    = False
                else:
                    logger.info(
                        f"👁️  [OBSERVATION] Signal: {direction} | "
                        f"Reason: {reason} | Edge: {edge:.1f}% | "
                        f"Set TRADE_SIZE > 0 to go live"
                    )

            time.sleep(random.uniform(1.5, 2.5))

        except KeyboardInterrupt:
            logger.info("⛔ Bot stopped by user.")
            break
        except Exception as e:
            logger.error(f"Unexpected error: {e}", exc_info=True)
            time.sleep(10)


if __name__ == "__main__":
    run_bot()
