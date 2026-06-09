"""Structure layer — spec §4.3. Mechanical ICT read.

NOTE (honesty, spec §10 + §1.2): the precise version operates on a dedicated 1m/5m
websocket spot feed (open/high/low/close + futures volume). That feed is a deferred
master-plan phase. Until it lands, this runs on the 60s chain spot as close-only bars,
so sweeps/FVG/order-blocks (which need intrabar highs/lows) are limited or skipped.
It still supplies swing structure, MSS, premium/discount, trigger and invalidation.

Structure NEVER votes on direction (spec §4.3) — it returns trigger, invalidation and an
AGREE/DISAGREE/NEUTRAL flag versus the Direction engine's side.
"""

from __future__ import annotations

AGREE, DISAGREE, NEUTRAL = "AGREE", "DISAGREE", "NEUTRAL"


def _swings(closes: list[float], k: int):
    """k-bar fractal swing highs/lows on a close-only series -> (highs, lows) as (idx, value)."""
    highs, lows = [], []
    for i in range(k, len(closes) - k):
        window = closes[i - k:i + k + 1]
        c = closes[i]
        if c == max(window) and window.count(c) == 1:
            highs.append((i, c))
        if c == min(window) and window.count(c) == 1:
            lows.append((i, c))
    return highs, lows


def read_structure(spot_bars: list[dict], direction: str, params) -> dict:
    closes = [float(b["close"]) for b in spot_bars if b.get("close")]
    out = {
        "last_mss_dir": None, "in_zone": None, "recent_sweep": None,
        "entry_trigger": None, "invalidation": None,
        "agreement": NEUTRAL, "quality": "interim_close_only", "bars": len(closes),
    }
    k = params.k_fractal
    if len(closes) < 2 * k + 2:
        out["note"] = "warming up (not enough bars for structure)"
        return out

    highs, lows = _swings(closes, k)
    bsl = highs[-1][1] if highs else None     # buy-side liquidity (recent swing high)
    ssl = lows[-1][1] if lows else None       # sell-side liquidity (recent swing low)
    last = closes[-1]

    # premium / discount within the current dealing range
    if bsl is not None and ssl is not None and bsl > ssl:
        pos = (last - ssl) / (bsl - ssl)
        out["in_zone"] = "premium" if pos > 0.55 else "discount" if pos < 0.45 else "equilibrium"

    # market structure shift: a close beyond the most recent opposing swing
    if bsl is not None and last > bsl:
        out["last_mss_dir"] = "BULL"
        out["entry_trigger"] = {"type": "MSS", "level": round(bsl, 2),
                                "condition": "close holds above swing high"}
        out["invalidation"] = round(ssl, 2) if ssl is not None else None
    elif ssl is not None and last < ssl:
        out["last_mss_dir"] = "BEAR"
        out["entry_trigger"] = {"type": "MSS", "level": round(ssl, 2),
                                "condition": "close holds below swing low"}
        out["invalidation"] = round(bsl, 2) if bsl is not None else None

    # coarse liquidity sweep (close-only): exceeded a pool in the last n_sweep bars then closed back
    recent = closes[-(params.n_sweep + 1):]
    if bsl is not None and max(recent) > bsl >= last:
        out["recent_sweep"] = {"side": "BSL", "level": round(bsl, 2)}
    elif ssl is not None and min(recent) < ssl <= last:
        out["recent_sweep"] = {"side": "SSL", "level": round(ssl, 2)}

    # agreement vs direction side
    side = {"BULL": "BULL", "BEAR": "BEAR"}.get(direction)
    if out["last_mss_dir"] and side:
        out["agreement"] = AGREE if out["last_mss_dir"] == side else DISAGREE

    return out
