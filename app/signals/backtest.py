"""Backtest / calibration harness — spec S-E.

Replays recorded chain snapshots through the SAME engine used live (spec §0.3, one code
path), then evaluates how actionable stances would have played out using forward spot moves
already present in the recorded data. Used to (a) see the action/conviction distribution and
(b) tune thresholds before trusting the live output.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from .engine import compute_stance
from .metrics import _to_dt
from .params import params_for
from .reconcile import reconcile


def replay(snaps: list[dict], params=None) -> list[tuple[dict, dict]]:
    """snaps oldest->newest for ONE underlying. Returns [(snap, engine_output), ...]."""
    results, history = [], []
    day_open, cur_date, prev_regime = None, None, None
    for snap in snaps:
        ts = _to_dt(snap["ts_ist"])
        d = ts.date()
        if d != cur_date:          # new IST day -> reset baselines
            cur_date, day_open, history = d, snap, []
        out = compute_stance(snap, day_open, history[-30:], prev_regime, now_ist=ts, params=params)
        prev_regime = out["stance"]["regime"]
        results.append((snap, out))
        history.append(snap)
    return results


def evaluate(results: list[tuple[dict, dict]], horizon_min: int = 15) -> dict:
    """Distribution of stances + forward-move hit rate for actionable directional calls."""
    times = [_to_dt(s["ts_ist"]) for s, _ in results]
    spots = [float(s.get("spot") or 0) for s, _ in results]
    n = len(results)

    actions: dict[str, int] = {}
    regimes: dict[str, int] = {}
    convs: list[float] = []
    actionable = evaluated = hits = 0
    moves: list[float] = []

    for i, (snap, out) in enumerate(results):
        st = out["stance"]
        actions[st["action"]] = actions.get(st["action"], 0) + 1
        regimes[st["regime"]] = regimes.get(st["regime"], 0) + 1
        convs.append(st.get("conviction") or 0.0)

        if st["action"] in ("TRADE_WITH_BIAS", "FADE_TO_PIN") and st.get("side") in ("LONG", "SHORT"):
            actionable += 1
            target = times[i] + timedelta(minutes=horizon_min)
            j = i
            while j < n and times[j] < target and times[j].date() == times[i].date():
                j += 1
            if j < n and times[j].date() == times[i].date():
                move = spots[j] - spots[i]
                moves.append(move if st["side"] == "LONG" else -move)
                evaluated += 1
                if (move > 0 and st["side"] == "LONG") or (move < 0 and st["side"] == "SHORT"):
                    hits += 1

    cs = sorted(convs)

    def pct(q: float):
        return round(cs[min(len(cs) - 1, int(q * len(cs)))], 4) if cs else None

    return {
        "snapshots": n,
        "span": {"from": results[0][0].get("ts_ist") if n else None,
                 "to": results[-1][0].get("ts_ist") if n else None},
        "actions": actions,
        "regimes": regimes,
        "conviction": {"p50": pct(0.5), "p90": pct(0.9), "p99": pct(0.99),
                       "max": round(max(convs), 4) if convs else None},
        "actionable": actionable,
        "evaluated": evaluated,
        "hit_rate": round(hits / evaluated, 3) if evaluated else None,
        "avg_signed_move_pts": round(sum(moves) / len(moves), 2) if moves else None,
        "horizon_min": horizon_min,
    }


# ---------- realistic option-P&L backtest with out-of-sample split + threshold sweep ----------

def _leg(snap: dict, strike, cp: str, field: str):
    for s in snap.get("strikes", []):
        if s["strike"] == strike:
            v = (s.get(cp) or {}).get(field)
            return float(v) if v is not None else None
    return None


def prepare(snaps: list[dict], horizon_min: int) -> list[dict]:
    """Replay once (layer outputs are threshold-independent) and precompute each candidate
    trade's real entry/exit option premiums, so a threshold sweep is cheap and honest."""
    base = replay(snaps)
    times = [_to_dt(s["ts_ist"]) for s, _ in base]
    n = len(base)
    recs = []
    for i, (snap, out) in enumerate(base):
        atm = out["metrics"].get("atm")
        ce_ask, pe_ask = _leg(snap, atm, "ce", "ask"), _leg(snap, atm, "pe", "ask")
        ce_ltp, pe_ltp = _leg(snap, atm, "ce", "ltp"), _leg(snap, atm, "pe", "ltp")
        # exit snapshot ~horizon later, same day
        target = times[i] + timedelta(minutes=horizon_min)
        j = i
        while j < n and times[j] < target and times[j].date() == times[i].date():
            j += 1
        exit_ok = j < n and times[j].date() == times[i].date()
        recs.append({
            "ts": times[i], "out": out,
            "entry_ce": ce_ask if (ce_ask and ce_ask > 0) else ce_ltp,   # buy at ask (fallback ltp)
            "entry_pe": pe_ask if (pe_ask and pe_ask > 0) else pe_ltp,
            "exit_ce": _leg(base[j][0], atm, "ce", "bid") if exit_ok else None,  # sell at bid
            "exit_pe": _leg(base[j][0], atm, "pe", "bid") if exit_ok else None,
            "exit_ok": exit_ok,
        })
    return recs


def _trade_pnl(rec: dict, p, cost: float):
    """Premium-points P&L for the option this stance would have bought, or None if no trade."""
    out = rec["out"]
    st = reconcile(out["metrics"], out["regime"], out["direction"], out["structure"], p,
                   data_stale=False, now_ist=rec["ts"])
    if st["action"] not in ("TRADE_WITH_BIAS", "FADE_TO_PIN") or st["side"] not in ("LONG", "SHORT"):
        return None
    if not rec["exit_ok"]:
        return None
    entry = rec["entry_ce"] if st["side"] == "LONG" else rec["entry_pe"]
    exit_ = rec["exit_ce"] if st["side"] == "LONG" else rec["exit_pe"]
    if not entry or entry <= 0 or exit_ is None:
        return None
    return exit_ - entry - cost          # buy ask, sell bid, minus brokerage/taxes estimate


def _agg(subset: list[dict], p, cost: float) -> dict:
    pnls = [x for x in (_trade_pnl(r, p, cost) for r in subset) if x is not None]
    if not pnls:
        return {"trades": 0, "win_rate": None, "avg_pnl_pts": None, "total_pnl_pts": None}
    wins = sum(1 for x in pnls if x > 0)
    return {"trades": len(pnls), "win_rate": round(wins / len(pnls), 3),
            "avg_pnl_pts": round(sum(pnls) / len(pnls), 2), "total_pnl_pts": round(sum(pnls), 1)}


def sweep(snaps: list[dict], horizon_min: int = 15, cost: float = 1.0, train_frac: float = 0.7,
          floors=(0.05, 0.10, 0.15, 0.20, 0.25), convs=(0.15, 0.25, 0.35, 0.45)) -> dict:
    """Tune thresholds on the first train_frac of the data, score on the held-out remainder.

    P&L is in premium POINTS per 1 unit (multiply by lot size for rupees). It already includes
    the bid/ask spread (buy ask, sell bid); `cost` adds an estimate for brokerage + taxes.
    """
    recs = prepare(snaps, horizon_min)
    if not recs:
        return {"error": "no data"}
    p0 = params_for(snaps[0].get("underlying", "NIFTY"))
    cut = max(1, int(len(recs) * train_frac))
    train, test = recs[:cut], recs[cut:]

    grid = []
    for fl in floors:
        for cv in convs:
            p = replace(p0, theta_floor=fl, theta_conv=cv)
            grid.append({"floor": fl, "conv": cv,
                         "train": _agg(train, p, cost), "test": _agg(test, p, cost)})

    eligible = [g for g in grid if (g["train"]["trades"] or 0) >= 5]
    best = max(eligible or grid, key=lambda g: (g["train"]["total_pnl_pts"] if g["train"]["total_pnl_pts"] is not None else -1e9))

    return {
        "underlying": snaps[0].get("underlying"),
        "snapshots": len(recs),
        "span": {"from": snaps[0].get("ts_ist"), "to": snaps[-1].get("ts_ist")},
        "horizon_min": horizon_min, "cost_pts": cost, "train_frac": train_frac,
        "best_thresholds_tuned_on_train": {"floor": best["floor"], "conv": best["conv"]},
        "train_result": best["train"],
        "test_result_OUT_OF_SAMPLE": best["test"],
        "grid_sorted_by_train": sorted(
            grid, key=lambda g: (g["train"]["total_pnl_pts"] if g["train"]["total_pnl_pts"] is not None else -1e9),
            reverse=True),
        "note": "Honest number is test_result_OUT_OF_SAMPLE. Compare it to train_result to see overfitting.",
    }
