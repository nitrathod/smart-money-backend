"""Layer engines — spec §4.1 (regime gate) and §4.2 (direction engine). Pure functions.

The regime gate uses only observable inputs (pin concentration + vol behaviour). Dealer-sign
GEX is deliberately NOT used — it is unobservable from anonymous NSE OI (spec §10).
"""

from __future__ import annotations

PINNED, TRENDING, NEUTRAL_R = "PINNED", "TRENDING", "NEUTRAL"
BULL, BEAR, NEUTRAL = "BULL", "BEAR", "NEUTRAL"


def _clamp(x: float, lo=0.0, hi=1.0) -> float:
    return max(lo, min(hi, x))


def classify_regime(m: dict, prev_regime: str | None, p) -> dict:
    spot, pin = m["spot"], m["pin"]
    em_rem = m.get("em_rem") or 0.0
    iv_atm, iv_base, rv = m.get("iv_atm"), m.get("iv_base"), m.get("rv")

    denom = max(0.5 * em_rem, 1e-6)
    near_pin = _clamp(1.0 - abs(spot - pin) / denom)

    term1 = ((iv_base - iv_atm) / iv_base) if (iv_base and iv_atm) else 0.0
    term2 = (1.0 - rv / iv_atm) if (rv and iv_atm) else 0.0
    pinned_vol = _clamp(p.k1 * term1 + p.k2 * term2)

    straddle_slope = m.get("straddle_slope", 0.0) or 0.0
    compression = 1.0 if straddle_slope < 0 else 0.0
    expansion = 1.0 if straddle_slope > 0 else 0.0
    iv_spike = 1.0 if m.get("iv_expansion") else 0.0
    magnet = m.get("pin_conc", 0.0)

    pinned_score = (near_pin + pinned_vol + compression + magnet) / 4.0
    trending_score = ((1 - near_pin) + (1 - pinned_vol) + expansion + iv_spike) / 4.0

    # hysteresis on near_pin (spec §4.1): enter PINNED only above pin_enter; stay until below pin_exit
    if prev_regime == PINNED and near_pin >= p.pin_exit:
        regime = PINNED
    elif near_pin > p.pin_enter and pinned_score >= p.theta_pin and pinned_score > trending_score:
        regime = PINNED
    elif trending_score >= p.theta_trend and trending_score > pinned_score:
        regime = TRENDING
    else:
        regime = NEUTRAL_R

    return {
        "regime": regime,
        "regime_confidence": round(abs(pinned_score - trending_score), 4),
        "pinned_score": round(pinned_score, 4),
        "trending_score": round(trending_score, 4),
        "near_pin": round(near_pin, 4),
        "pinned_vol": round(pinned_vol, 4),
    }


def aggregate_direction(atoms: dict, regime: str, p) -> dict:
    # weight table; max-pain only inside PINNED (spec §3, §4.2)
    included = {
        "oi_buildup": p.w_oi_buildup,
        "pcr_slope": p.w_pcr_slope,
        "momentum": p.w_momentum,
        "wall": p.w_wall,
    }
    if regime == PINNED:
        included["maxpain_pull"] = p.w_maxpain

    num = den = 0.0
    contribs = {}
    signs = []
    for name, w in included.items():
        a = atoms[name]
        s = a["strength"] * (1 if a["direction"] == BULL else -1 if a["direction"] == BEAR else 0)
        contribs[name] = round(s, 4)
        num += w * s
        den += w
        if s != 0:
            signs.append(1 if s > 0 else -1)

    score = (num / den) if den else 0.0
    total = len(included)
    if score != 0 and signs:
        sign = 1 if score > 0 else -1
        agree = sum(1 for x in signs if x == sign)
        disagree = sum(1 for x in signs if x != sign)
        alignment = max(0.0, (agree - disagree) / total)
    else:
        alignment = 0.0
    conviction = _clamp(abs(score) * alignment)

    direction = BULL if score > p.eps_dir else BEAR if score < -p.eps_dir else NEUTRAL
    return {
        "direction": direction,
        "score": round(score, 4),
        "conviction": round(conviction, 4),
        "alignment": round(alignment, 4),
        "contributions": contribs,
    }
