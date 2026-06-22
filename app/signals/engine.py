"""Engine orchestrator — spec §0.3 'one code path'. Live and backtest both call this.

compute_stance(snap, day_open, history, prev_regime, now_ist) -> {
    metrics, atoms, regime, direction, structure, stance
}
Only `stance` is the public Oracle output (§1.4); the rest is returned for audit/UI/debug.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from .atomic import compute_atoms
from .layers import aggregate_direction, classify_regime
from .metrics import _to_dt, compute_metrics
from .params import params_for
from .reconcile import reconcile
from .structure import read_structure

IST = ZoneInfo("Asia/Kolkata")


def _spot_bars(series: list[dict]) -> list[dict]:
    bars = []
    for s in series:
        spot = s.get("spot")
        if spot:
            bars.append({"ts": s.get("ts_ist"), "close": float(spot)})
    return bars


def compute_stance(snap: dict, day_open: dict | None, history: list[dict],
                   prev_regime: str | None = None, now_ist: datetime | None = None,
                   params=None) -> dict:
    p = params or params_for(snap.get("underlying", "NIFTY"))
    now_ist = now_ist or datetime.now(IST)
    ts = _to_dt(snap.get("ts_ist"))
    age = (now_ist - ts).total_seconds()
    data_stale = age > p.freshness_sec

    metrics = compute_metrics(snap, day_open, history, p)

    if metrics.get("insufficient"):
        stance = {
            "underlying": snap.get("underlying"), "ts_ist": metrics.get("ts_ist"),
            "regime": "NEUTRAL", "direction": "NEUTRAL", "conviction": 0.0,
            "action": "STAND_ASIDE", "side": "NONE", "size_factor": 0.0,
            "why": ["insufficient chain data"], "inputs_ref": None,
        }
        return {"metrics": metrics, "atoms": {}, "regime": {}, "direction": {},
                "structure": {}, "stance": stance}

    atoms = compute_atoms(snap, metrics, p)
    regime_out = classify_regime(metrics, prev_regime, p)
    direction_out = aggregate_direction(atoms, regime_out["regime"], p)
    structure = read_structure(_spot_bars(list(history) + [snap]), direction_out["direction"], p)
    stance = reconcile(metrics, regime_out, direction_out, structure, p,
                       data_stale=data_stale, now_ist=now_ist)

    # strip internals not meant for storage/display
    metrics_public = {k: v for k, v in metrics.items() if not k.startswith("_")}
    return {
        "metrics": metrics_public, "atoms": atoms, "regime": regime_out,
        "direction": direction_out, "structure": structure, "stance": stance,
    }
