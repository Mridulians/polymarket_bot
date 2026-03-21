"""
risk_manager.py
───────────────
Enforces trading limits to prevent runaway losses.

Tracks:
  - Daily trade count (hard cap)
  - Total capital deployed today
  - Win/Loss ratio (circuit breaker)
"""

import logging
from datetime import datetime, date

logger = logging.getLogger("polybot")


class RiskManager:
    def __init__(self, max_daily_trades: int, max_position_usdc: float):
        self.max_daily_trades   = max_daily_trades
        self.max_position_usdc  = max_position_usdc

        # Daily stats (reset each day)
        self._today             = date.today()
        self._trades_today      = 0
        self._capital_deployed  = 0.0
        self._wins              = 0
        self._losses            = 0

        # Circuit breaker: stop trading if 3 consecutive losses
        self._consecutive_losses = 0
        self._CIRCUIT_BREAK_AT   = 3

    def _maybe_reset_daily(self):
        """Reset counters at midnight."""
        today = date.today()
        if today != self._today:
            logger.info(f"📅 New day. Resetting risk counters. "
                        f"Yesterday: {self._trades_today} trades, "
                        f"{self._wins}W/{self._losses}L")
            self._today              = today
            self._trades_today       = 0
            self._capital_deployed   = 0.0
            self._wins               = 0
            self._losses             = 0
            self._consecutive_losses = 0

    def can_trade(self) -> bool:
        """Returns True if all risk checks pass."""
        self._maybe_reset_daily()

        if self._trades_today >= self.max_daily_trades:
            logger.warning(f"Daily trade limit reached ({self.max_daily_trades}). No more trades today.")
            return False

        if self._consecutive_losses >= self._CIRCUIT_BREAK_AT:
            logger.warning(
                f"🔴 Circuit breaker! {self._consecutive_losses} consecutive losses. "
                f"Trading halted for the session."
            )
            return False

        return True

    def record_trade(self, capital_used: float):
        """Log a new trade and update capital tracking."""
        self._trades_today     += 1
        self._capital_deployed += capital_used
        logger.info(
            f"📊 Trade #{self._trades_today} recorded. "
            f"Capital deployed today: ${self._capital_deployed:.2f} USDC"
        )

    def record_result(self, won: bool):
        """Record outcome of a trade for circuit breaker logic."""
        if won:
            self._wins             += 1
            self._consecutive_losses = 0
            logger.info(f"✅ Win recorded. Wins: {self._wins} | Losses: {self._losses}")
        else:
            self._losses             += 1
            self._consecutive_losses += 1
            logger.warning(
                f"❌ Loss recorded. Consecutive losses: {self._consecutive_losses}. "
                f"Circuit breaker triggers at {self._CIRCUIT_BREAK_AT}."
            )

    def summary(self) -> dict:
        """Return a snapshot of today's risk stats."""
        return {
            "trades_today":       self._trades_today,
            "capital_deployed":   self._capital_deployed,
            "wins":               self._wins,
            "losses":             self._losses,
            "consecutive_losses": self._consecutive_losses,
        }
