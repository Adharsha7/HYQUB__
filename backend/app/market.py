from __future__ import annotations
import asyncio
import math
import time
from typing import Any

import httpx2 as httpx

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_CACHE_TTL      = 300   # seconds – good-result TTL (5 minutes)
_RETRY_DEMO_TTL = 30    # seconds – retry after providers fail (30 seconds)
_MIN_POINTS     = 7
_MAX_POINTS     = 90

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------
_cache_lock: asyncio.Lock | None = None

# Regular TTL cache
_cache_data: dict[str, Any] | None = None
_cache_ts: float = 0.0
_cache_ttl: float = _CACHE_TTL

# Last known-good result from a real provider (binance or kraken)
_last_good: dict[str, Any] | None = None


def _get_lock() -> asyncio.Lock:
    global _cache_lock
    if _cache_lock is None:
        _cache_lock = asyncio.Lock()
    return _cache_lock


# ---------------------------------------------------------------------------
# Demo / fallback series
# ---------------------------------------------------------------------------

def _demo_series(base: float = 2500.0) -> list[list[float]]:
    """Deterministic 90-point [[ms, price], ...] series (seeded LCG)."""
    seed = 42
    points: list[list[float]] = []
    now_ms   = int(time.time() * 1000)
    day_ms   = 86_400_000
    today_ms = (now_ms // day_ms) * day_ms
    price    = base * 0.92
    for i in range(89, -1, -1):
        seed = (seed + 0x6D2B79F5) & 0xFFFFFFFF
        t = (seed ^ (seed >> 15)) * (1 | seed) & 0xFFFFFFFF
        t = (t + ((t ^ (t >> 7)) * (61 | t) & 0xFFFFFFFF)) ^ t & 0xFFFFFFFF
        r = ((t ^ (t >> 14)) & 0xFFFFFFFF) / 4_294_967_296
        price *= 1 + (r - 0.48) * 0.05
        points.append([float(today_ms - i * day_ms), round(price, 2)])
    return points


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate(points: list[list[Any]]) -> None:
    """
    Raise ValueError if points fail any of:
      - not a list or length < _MIN_POINTS or > _MAX_POINTS
      - timestamps are not strictly ascending integers in ms
      - any price is not a finite float > 0
    """
    if not isinstance(points, list):
        raise ValueError("points is not a list")
    n = len(points)
    if n < _MIN_POINTS or n > _MAX_POINTS:
        raise ValueError(f"point count {n} outside [{_MIN_POINTS}, {_MAX_POINTS}]")
    prev_ts: int | None = None
    for pt in points:
        if not isinstance(pt, (list, tuple)) or len(pt) < 2:
            raise ValueError("invalid point structure")
        ms, price = pt[0], pt[1]
        if isinstance(ms, bool) or not isinstance(ms, (int, float)) or not math.isfinite(ms) or ms != int(ms):
            raise ValueError(f"timestamp {ms!r} is not an integer")
        ms_int = int(ms)
        if prev_ts is not None and ms_int <= prev_ts:
            raise ValueError(f"timestamps not strictly ascending: {ms} after {prev_ts}")
        prev_ts = ms_int
        if isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
            raise ValueError(f"invalid price {price!r}")


# ---------------------------------------------------------------------------
# Fetch helpers
# ---------------------------------------------------------------------------

async def _fetch_binance(client: httpx.AsyncClient) -> list[list[float]]:
    resp = await client.get(
        "https://api.binance.com/api/v3/klines",
        params={"symbol": "ETHUSDT", "interval": "1d", "limit": _MAX_POINTS},
        timeout=8,
    )
    if resp.status_code != 200:
        raise ValueError(f"Binance HTTP {resp.status_code}")
    rows = resp.json()
    if not isinstance(rows, list) or not rows:
        raise ValueError("Binance: bad response")
    points: list[list[float]] = []
    for r in rows:
        if not isinstance(r, (list, tuple)) or len(r) < 5:
            raise ValueError("Binance: invalid row format")
        try:
            ms = int(r[0])
            price = float(r[4])
        except (ValueError, TypeError):
            raise ValueError("Binance: failed to parse row numbers")
        points.append([ms, price])
    _validate(points)
    return points


async def _fetch_kraken(client: httpx.AsyncClient) -> list[list[float]]:
    resp = await client.get(
        "https://api.kraken.com/0/public/OHLC",
        params={"pair": "ETHUSD", "interval": 1440},
        timeout=8,
    )
    if resp.status_code != 200:
        raise ValueError(f"Kraken HTTP {resp.status_code}")
    body = resp.json()
    if body.get("error"):
        raise ValueError(f"Kraken error: {body['error']}")
    result = body.get("result", {})
    result.pop("last", None)
    if not result:
        raise ValueError("Kraken: no OHLC key")
    rows = next(iter(result.values()))
    if not isinstance(rows, list) or not rows:
        raise ValueError("Kraken: empty array")
    points: list[list[float]] = []
    for r in rows[-_MAX_POINTS:]:
        if not isinstance(r, (list, tuple)) or len(r) < 5:
            raise ValueError("Kraken: invalid row format")
        try:
            ms = int(float(r[0]) * 1000)
            price = float(r[4])
        except (ValueError, TypeError):
            raise ValueError("Kraken: failed to parse row numbers")
        points.append([ms, price])
    _validate(points)
    return points


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def get_eth_price_series() -> dict[str, Any]:
    """
    Return {"source": str, "points": list | None}.

    Priority:
      1. Cache hit  -> return cached (within TTL)
      2. Binance    -> validate -> cache + update _last_good (5 min TTL)
      3. Kraken     -> validate -> cache + update _last_good (5 min TTL)
      4. _last_good -> return stale-if-error (30 s retry TTL)
      5. Demo sentinel {"source": "demo", "points": None} (30 s retry TTL)
    """
    global _cache_data, _cache_ts, _cache_ttl, _last_good

    lock = _get_lock()
    async with lock:
        now = time.monotonic()
        if _cache_data is not None and (now - _cache_ts) < _cache_ttl:
            return _cache_data

        result: dict[str, Any] | None = None
        async with httpx.AsyncClient() as client:
            try:
                pts = await _fetch_binance(client)
                result = {"source": "binance", "points": pts}
            except Exception:
                try:
                    pts = await _fetch_kraken(client)
                    result = {"source": "kraken", "points": pts}
                except Exception:
                    pass

        if result is not None:
            # Good provider result
            _last_good = result
            _cache_data = result
            _cache_ts   = time.monotonic()
            _cache_ttl  = _CACHE_TTL
            return result

        # Both providers failed
        if _last_good is not None:
            # Stale-if-error: return previous good data, retry after 30s
            _cache_data = _last_good
            _cache_ts   = time.monotonic()
            _cache_ttl  = _RETRY_DEMO_TTL
            return _last_good

        # No good data ever – return demo sentinel, retry after 30s
        sentinel = {"source": "demo", "points": None}
        _cache_data = sentinel
        _cache_ts   = time.monotonic()
        _cache_ttl  = _RETRY_DEMO_TTL
        return sentinel
