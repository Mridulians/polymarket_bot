"""
market_scanner.py
─────────────────
FIXED: Uses deterministic slug calculation to find BTC Up/Down markets.

KEY INSIGHT: Polymarket's 15-min market slugs are NOT found by searching
the API — they are CALCULATED from the current Unix timestamp:

    slug = f"btc-updown-15m-{round_down_to_15min(now)}"

This is because markets are created on a fixed schedule every 15 minutes,
so we can always compute the slug directly without any API search.

Reference: https://github.com/handiko/Polymarket-Market-Finder
"""

import time
import logging
import requests
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger("polybot")

GAMMA_API = "https://gamma-api.polymarket.com"


class MarketScanner:
    def __init__(self, poly_client):
        self.client          = poly_client
        self._cached_market  = None
        self._cached_slug    = None

    def _round_down(self, ts: int, interval_seconds: int) -> int:
        """Round a Unix timestamp down to the nearest interval boundary."""
        return ts - (ts % interval_seconds)

    def _build_slug(self, asset: str, interval: str, ts: int) -> str:
        """
        Build a Polymarket market slug deterministically.
        Examples:
          btc-updown-15m-1771168500
          btc-updown-5m-1771168800
          eth-updown-15m-1771168500
        """
        return f"{asset.lower()}-updown-{interval}-{ts}"

    def _fetch_market_by_slug(self, slug: str) -> Optional[dict]:
        """
        Fetch a specific market from Gamma API using its slug.
        Returns market dict or None.
        """
        try:
            resp = requests.get(
                f"{GAMMA_API}/events",
                params={"slug": slug},
                timeout=10
            )
            resp.raise_for_status()
            data = resp.json()

            # Handle list or dict response
            if isinstance(data, list):
                events = data
            else:
                events = data.get("events", data.get("data", []))

            if not events:
                return None

            event = events[0]
            return event

        except Exception as e:
            logger.debug(f"Slug fetch failed for {slug}: {e}")
            return None

    def find_best_market(self, asset: str = "BTC",
                         window_minutes: int = 20) -> Optional[dict]:
        """
        Find the current active Up/Down market by calculating its slug.

        Tries slugs for: current 15m window, next 15m window,
        current 5m window, next 5m window.
        """
        now = int(time.time())
        asset_lower = asset.lower()

        # Build candidate slugs to try
        # 15-minute markets (interval = 900 seconds)
        ts_15m_current = self._round_down(now, 900)
        ts_15m_next    = ts_15m_current + 900
        ts_15m_prev    = ts_15m_current - 900

        # 5-minute markets (interval = 300 seconds)
        ts_5m_current  = self._round_down(now, 300)
        ts_5m_next     = ts_5m_current + 300
        ts_5m_prev     = ts_5m_current - 300

        slugs_to_try = [
            (self._build_slug(asset_lower, "15m", ts_15m_current), 900, ts_15m_current + 900),
            (self._build_slug(asset_lower, "15m", ts_15m_next),    900, ts_15m_next    + 900),
            (self._build_slug(asset_lower, "15m", ts_15m_prev),    900, ts_15m_prev    + 900),
            (self._build_slug(asset_lower, "5m",  ts_5m_current),  300, ts_5m_current  + 300),
            (self._build_slug(asset_lower, "5m",  ts_5m_next),     300, ts_5m_next     + 300),
            (self._build_slug(asset_lower, "5m",  ts_5m_prev),     300, ts_5m_prev     + 300),
        ]

        logger.info(f"Trying {len(slugs_to_try)} deterministic slugs for {asset}...")

        for slug, interval_sec, expected_expiry in slugs_to_try:
            time_to_expiry = expected_expiry - now
            if time_to_expiry < 30:
                continue  # Already expired

            logger.debug(f"Trying slug: {slug}")
            event = self._fetch_market_by_slug(slug)

            if event is None:
                logger.debug(f"  → Not found: {slug}")
                continue

            logger.info(f"  ✅ Found: {slug}")

            # Extract token IDs from the event's markets
            markets   = event.get("markets", [])
            token_ids = []

            for m in markets:
                clob = m.get("clobTokenIds", [])
                if isinstance(clob, str):
                    import json
                    try:
                        clob = json.loads(clob)
                    except Exception:
                        clob = [clob]
                token_ids.extend(clob)

            if len(token_ids) < 2:
                logger.warning(f"  Market found but not enough token IDs: {token_ids}")
                # Try to get from outcomes directly
                for m in markets:
                    tid = m.get("conditionId") or m.get("id")
                    if tid:
                        token_ids.append(str(tid))

            if len(token_ids) < 2:
                logger.warning(f"  Skipping {slug} — cannot get token IDs")
                continue

            timeframe_mins = interval_sec // 60

            result = {
                "token_id_up":    token_ids[0],
                "token_id_down":  token_ids[1],
                "expiry_ts":      expected_expiry,
                "time_to_exp":    time_to_expiry,
                "timeframe_mins": timeframe_mins,
                "description":    event.get("title", slug),
                "slug":           slug,
                "liquidity":      float(event.get("liquidity") or 0),
                "volume":         float(event.get("volume") or 0),
            }

            logger.info(
                f"🎯 Market ready: '{result['description']}' | "
                f"Timeframe: {timeframe_mins}min | "
                f"Expires in: {time_to_expiry // 60}m {time_to_expiry % 60}s"
            )
            return result

        # If all slugs failed, log what we tried
        logger.warning(f"Could not find any active {asset} Up/Down market.")
        logger.info("Slugs tried:")
        for slug, _, _ in slugs_to_try:
            logger.info(f"  → {slug}")
        logger.info("Tip: Check polymarket.com/crypto/15M to confirm markets are live.")
        return None