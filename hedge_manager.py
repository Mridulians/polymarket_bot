"""
hedge_manager.py
────────────────
Manages delta-neutral hedging via Binance Perpetual Futures.

When we buy YES shares on Polymarket (Long BTC outcome),
we simultaneously SHORT BTC on Binance perpetuals.

This means:
  - If BTC drops below strike → we lose on Polymarket YES but
    GAIN on the Binance short. Net loss is minimised.
  - If BTC stays above strike → YES resolves to $1, Binance short
    loses a tiny amount, but overall we profit.

This is "Delta Neutral" trading — we profit from the
Polymarket mispricing, NOT from BTC's direction.
"""

import logging
import time
from typing import Optional

logger = logging.getLogger("polybot")


class HedgeManager:
    def __init__(self, binance_client, enabled: bool = True):
        self.exchange       = binance_client
        self.enabled        = enabled
        self.open_position  = None  # Tracks active hedge

    def open_short(self, symbol: str, size: float) -> bool:
        """
        Open a short position on Binance Perpetuals to hedge our
        Polymarket YES position.

        Args:
            symbol: e.g. "BTC/USDT"
            size:   BTC amount to short (e.g. 0.01)

        Returns:
            True if successful, False otherwise.
        """
        if not self.enabled:
            logger.info("Hedging disabled — skipping short.")
            return False

        if self.open_position:
            logger.warning("Hedge already open. Skipping duplicate.")
            return False

        # Convert to futures symbol format (BTC/USDT → BTC/USDT:USDT)
        futures_symbol = symbol if ":" in symbol else f"{symbol}:{symbol.split('/')[1]}"

        try:
            logger.info(f"🛡️  Opening hedge: SHORT {size} {symbol} on Binance Perps...")
            order = self.exchange.create_order(
                symbol    = futures_symbol,
                type      = "market",
                side      = "sell",   # Short = sell
                amount    = size,
                params    = {"reduceOnly": False}
            )
            self.open_position = {
                "symbol":    futures_symbol,
                "size":      size,
                "order_id":  order.get("id"),
                "entry_ts":  int(time.time()),
            }
            logger.info(f"✅ Hedge opened: Order ID {order.get('id')}")
            return True

        except Exception as e:
            logger.error(f"Hedge failed: {e}")
            return False

    def close_hedge(self) -> bool:
        """
        Close the open short position (buy back to flatten delta).
        Called after Polymarket position resolves.
        """
        if not self.enabled or not self.open_position:
            logger.info("No hedge to close.")
            return False

        symbol = self.open_position["symbol"]
        size   = self.open_position["size"]

        try:
            logger.info(f"🔓 Closing hedge: BUY {size} {symbol} (close short)...")
            order = self.exchange.create_order(
                symbol = symbol,
                type   = "market",
                side   = "buy",   # Buy back = close short
                amount = size,
                params = {"reduceOnly": True}
            )
            logger.info(f"✅ Hedge closed: Order ID {order.get('id')}")
            self.open_position = None
            return True

        except Exception as e:
            logger.error(f"Failed to close hedge: {e}")
            return False

    def get_hedge_pnl(self) -> Optional[float]:
        """
        Fetch the unrealised PnL on the current hedge position.
        Returns USDT value or None if no position / error.
        """
        if not self.open_position:
            return None

        try:
            positions = self.exchange.fetch_positions([self.open_position["symbol"]])
            for pos in positions:
                if pos.get("symbol") == self.open_position["symbol"] and float(pos.get("contracts", 0)) != 0:
                    pnl = float(pos.get("unrealizedPnl", 0))
                    logger.debug(f"Hedge PnL: ${pnl:.4f} USDT")
                    return pnl
            return 0.0
        except Exception as e:
            logger.warning(f"Could not fetch hedge PnL: {e}")
            return None
