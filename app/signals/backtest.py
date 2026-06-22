"""Backtest / calibration harness — spec S-E.

Replays recorded chain snapshots through the SAME engine used live (spec §0.3, one code
path), then evaluates how actionable stances would have played out using forward spot moves
already present in the recorded data. Used to (a) see the action/conviction distribution and
(b) tune thresholds before trusting the live output.
"""

from __future__ import annotations

from datetime import timedelta

from .engine import compute_stance
from .metrics import _to_dt


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
