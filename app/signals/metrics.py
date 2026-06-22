"""Metric layer — spec §2. Pure functions: (snapshot, day_open, history, params) -> metrics dict.

Everything here is deterministic. OI/ΔOI are kept in CONTRACTS. 'Writing' is judged by the
sign of Δoi only, never by premium (spec §0.4, §3.1).
"""

from __future__ import annotations

import hashlib
import math
from datetime import datetime, time
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
_CLOSE = time(15, 30)


# ---------- small helpers ----------

def _num(x, default: float = 0.0) -> float:
    try:
        return float(x) if x is not None else default
    except (TypeError, ValueError):
        return default


def _to_dt(v) -> datetime:
    if isinstance(v, datetime):
        dt = v
    elif isinstance(v, str):
        dt = datetime.fromisoformat(v)
    else:
        dt = datetime.now(IST)
    return dt if dt.tzinfo else dt.replace(tzinfo=IST)


def atm_strike(strikes: list[dict], spot: float) -> int:
    return min((s["strike"] for s in strikes), key=lambda k: abs(k - spot))


def _by_strike(snap: dict) -> dict[int, dict]:
    return {s["strike"]: s for s in snap.get("strikes", [])}


def _straddle_for(snap: dict) -> float | None:
    strikes = snap.get("strikes", [])
    if not strikes:
        return None
    atm = atm_strike(strikes, _num(snap.get("spot")))
    row = next((s for s in strikes if s["strike"] == atm), None)
    if not row:
        return None
    return _num(row["ce"].get("ltp")) + _num(row["pe"].get("ltp"))


def _iv_atm_for(snap: dict) -> float | None:
    strikes = snap.get("strikes", [])
    if not strikes:
        return None
    atm = atm_strike(strikes, _num(snap.get("spot")))
    row = next((s for s in strikes if s["strike"] == atm), None)
    if not row:
        return None
    ivs = [_num(row["ce"].get("iv")), _num(row["pe"].get("iv"))]
    ivs = [v for v in ivs if v > 0]
    return sum(ivs) / len(ivs) if ivs else None


def _doi_sums(snap: dict, open_map: dict[int, tuple[float, float]] | None):
    """Returns (sum_dCE, sum_dPE, per-strike dict). Δoi vs day-open OI; fallback prev_oi; else 0."""
    dce_total = dpe_total = 0.0
    per = {}
    for s in snap.get("strikes", []):
        k = s["strike"]
        ce_oi, pe_oi = _num(s["ce"].get("oi")), _num(s["pe"].get("oi"))
        if open_map and k in open_map:
            ce0, pe0 = open_map[k]
        else:
            ce0, pe0 = _num(s["ce"].get("prev_oi")), _num(s["pe"].get("prev_oi"))
        dce, dpe = ce_oi - ce0, pe_oi - pe0
        per[k] = (dce, dpe)
        dce_total += dce
        dpe_total += dpe
    return dce_total, dpe_total, per


def _pcr_doi_for(snap: dict, open_map) -> float | None:
    dce, dpe, _ = _doi_sums(snap, open_map)
    return (dpe / dce) if dce > 0 else None


def _slope(values: list[float]) -> float:
    """Signed slope of a short series (newest last): mean(2nd half) - mean(1st half)."""
    vals = [v for v in values if v is not None]
    if len(vals) < 2:
        return 0.0
    mid = len(vals) // 2
    first, second = vals[:mid] or vals[:1], vals[mid:]
    return (sum(second) / len(second)) - (sum(first) / len(first))


def _ema(values: list[float], span: int) -> float | None:
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    a = 2.0 / (span + 1)
    e = vals[0]
    for v in vals[1:]:
        e = a * v + (1 - a) * e
    return e


def _realized_vol(spots: list[float]) -> float | None:
    pts = [s for s in spots if s and s > 0]
    if len(pts) < 3:
        return None
    rets = [math.log(pts[i] / pts[i - 1]) for i in range(1, len(pts))]
    n = len(rets)
    mean = sum(rets) / n
    var = sum((r - mean) ** 2 for r in rets) / (n - 1)
    # per-minute stdev -> annualised (375 min/session, ~250 sessions)
    return math.sqrt(var) * math.sqrt(375 * 250) * 100.0  # in % to match iv units


# ---------- main ----------

def compute_metrics(snap: dict, day_open: dict | None, history: list[dict], params) -> dict:
    strikes = snap.get("strikes", [])
    spot = _num(snap.get("spot"))
    ts = _to_dt(snap.get("ts_ist"))
    if not strikes or spot <= 0:
        return {"underlying": snap.get("underlying"), "ts_ist": ts.isoformat(),
                "spot": spot, "insufficient": True}

    atm = atm_strike(strikes, spot)
    open_map = None
    if day_open:
        open_map = {s["strike"]: (_num(s["ce"].get("oi")), _num(s["pe"].get("oi")))
                    for s in day_open.get("strikes", [])}

    sum_ce_oi = sum(_num(s["ce"].get("oi")) for s in strikes)
    sum_pe_oi = sum(_num(s["pe"].get("oi")) for s in strikes)
    pcr_oi = (sum_pe_oi / sum_ce_oi) if sum_ce_oi > 0 else None

    dce, dpe, per_doi = _doi_sums(snap, open_map)
    pcr_doi = (dpe / dce) if dce > 0 else None

    # max pain
    def payout(K: int) -> float:
        tot = 0.0
        for s in strikes:
            k = s["strike"]
            tot += _num(s["ce"].get("oi")) * max(K - k, 0)
            tot += _num(s["pe"].get("oi")) * max(k - K, 0)
        return tot
    max_pain = min((s["strike"] for s in strikes), key=payout)

    atm_row = next(s for s in strikes if s["strike"] == atm)
    straddle = _num(atm_row["ce"].get("ltp")) + _num(atm_row["pe"].get("ltp"))
    iv_atm = _iv_atm_for(snap)

    # expected move + remaining
    now_t = ts.timetz()
    minutes_left = max(0, (datetime.combine(ts.date(), _CLOSE, IST) - ts).total_seconds() / 60.0)
    frac = min(1.0, max(0.0, minutes_left / params.session_minutes))
    em_full = straddle
    em_rem = em_full * math.sqrt(frac) if frac > 0 else 0.0

    # walls
    above = [s for s in strikes if s["strike"] > spot]
    below = [s for s in strikes if s["strike"] < spot]
    resistance_wall = max(above, key=lambda s: _num(s["ce"].get("oi")))["strike"] if above else None
    support_wall = max(below, key=lambda s: _num(s["pe"].get("oi")))["strike"] if below else None

    # gamma pin
    def gamma_oi(s: dict) -> float:
        g = (_num(s["ce"].get("gamma")) + _num(s["pe"].get("gamma"))) / 2.0
        return g * (_num(s["ce"].get("oi")) + _num(s["pe"].get("oi")))
    goi = {s["strike"]: gamma_oi(s) for s in strikes}
    goi_total = sum(goi.values())
    if goi_total > 0:
        pin = max(goi, key=goi.get)
        pin_conc = goi[pin] / goi_total
    else:  # no greeks (e.g. mock data) -> fall back to max total-OI strike
        pin = max(strikes, key=lambda s: _num(s["ce"].get("oi")) + _num(s["pe"].get("oi")))["strike"]
        pin_conc = 0.0

    # series / slopes (history oldest->newest, then current)
    series = list(history) + [snap]
    iv_series = [_iv_atm_for(s) for s in series]
    iv_base = _ema(iv_series, params.n_iv)
    straddle_series = [_straddle_for(s) for s in series[-params.n_slope:]]
    pcr_doi_series = [_pcr_doi_for(s, open_map) for s in series[-params.n_slope:]]
    straddle_slope = _slope(straddle_series)
    pcr_doi_slope = _slope(pcr_doi_series)

    spots = [_num(s.get("spot")) for s in series]
    rv = _realized_vol(spots[-min(len(spots), 30):])
    day_open_spot = _num(day_open.get("spot")) if day_open else (spots[0] if spots else spot)

    iv_expansion = bool(iv_atm and iv_base and iv_atm > iv_base * (1 + params.alpha))
    iv_crush = bool(iv_atm and iv_base and iv_atm < iv_base * (1 - params.alpha))

    inputs_ref = hashlib.md5(
        f"{snap.get('underlying')}|{ts.isoformat()}|{spot}|{pcr_oi}|{max_pain}|{pin}".encode()
    ).hexdigest()[:12]

    return {
        "underlying": snap.get("underlying"),
        "ts_ist": ts.isoformat(),
        "spot": round(spot, 2),
        "atm": atm,
        "day_open_spot": round(day_open_spot, 2),
        "pcr_oi": round(pcr_oi, 4) if pcr_oi is not None else None,
        "pcr_doi": round(pcr_doi, 4) if pcr_doi is not None else None,
        "pcr_doi_slope": round(pcr_doi_slope, 4),
        "max_pain": max_pain,
        "straddle": round(straddle, 2),
        "straddle_slope": round(straddle_slope, 4),
        "em_full": round(em_full, 2),
        "em_rem": round(em_rem, 2),
        "minutes_left": round(minutes_left, 1),
        "iv_atm": round(iv_atm, 4) if iv_atm is not None else None,
        "iv_base": round(iv_base, 4) if iv_base is not None else None,
        "iv_expansion": iv_expansion,
        "iv_crush": iv_crush,
        "resistance_wall": resistance_wall,
        "support_wall": support_wall,
        "pin": pin,
        "pin_conc": round(pin_conc, 4),
        "d_ce": round(dce, 1),
        "d_pe": round(dpe, 1),
        "rv": round(rv, 4) if rv is not None else None,
        "_per_doi": per_doi,          # internal: per-strike (dCE,dPE) for atomic signals
        "_open_map": open_map is not None,
        "history_len": len(history),
        "inputs_ref": inputs_ref,
    }
