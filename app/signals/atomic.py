"""Atomic signals — spec §3. Each: (snap, metrics, params) -> {direction, strength, note}.

direction ∈ {BULL, BEAR, NEUTRAL}; strength ∈ [0,1].
Signals 1,2,3,5 are directional (feed the Direction engine).
Signal 4 (max-pain pull) is directional but ONLY counted inside PINNED regime.
Signals 6,7 are non-directional regime inputs.
'Writing' is judged by Δoi sign only — never by premium (spec §0.4).
"""

from __future__ import annotations

import math

BULL, BEAR, NEUTRAL = "BULL", "BEAR", "NEUTRAL"


def _sat(x: float, scale: float) -> float:
    """Saturating norm to [0,1]."""
    return max(0.0, min(1.0, math.tanh(abs(x) / scale))) if scale else 0.0


def s1_oi_buildup(snap: dict, m: dict, p) -> dict:
    spot = m["spot"]
    per = m.get("_per_doi", {})
    ce_above = sum(dce for k, (dce, dpe) in per.items() if k > spot)
    pe_below = sum(dpe for k, (dce, dpe) in per.items() if k < spot)
    net = pe_below - ce_above            # PE writing below (support, BULL) vs CE writing above (BEAR)
    denom = abs(ce_above) + abs(pe_below) + 1.0
    direction = BULL if net > 0 else BEAR if net < 0 else NEUTRAL
    return {"direction": direction, "strength": _sat(net / denom, 0.5),
            "note": f"ΔCE>spot={ce_above:.0f} ΔPE<spot={pe_below:.0f}"}


def s2_pcr_slope(snap: dict, m: dict, p) -> dict:
    slope = m.get("pcr_doi_slope", 0.0) or 0.0
    direction = BULL if slope > 0 else BEAR if slope < 0 else NEUTRAL  # rising put-writing ⇒ BULL
    pcr_oi = m.get("pcr_oi")
    extreme = pcr_oi is not None and (pcr_oi > p.pcr_hi or pcr_oi < p.pcr_lo)
    note = f"pcr_doi slope={slope:.3f}" + (" [extreme pcr_oi]" if extreme else "")
    return {"direction": direction, "strength": _sat(slope, 0.3), "note": note}


def s3_momentum(snap: dict, m: dict, p) -> dict:
    spot, ref = m["spot"], m["day_open_spot"]
    em = m.get("em_full") or 1.0
    dist = spot - ref
    direction = BULL if dist > 0 else BEAR if dist < 0 else NEUTRAL
    return {"direction": direction, "strength": _sat(dist / em, 1.0),
            "note": f"spot {spot} vs open {ref}"}


def s4_maxpain_pull(snap: dict, m: dict, p) -> dict:
    spot, mp = m["spot"], m["max_pain"]
    diff = mp - spot
    direction = BULL if diff > 0 else BEAR if diff < 0 else NEUTRAL
    em_rem = m.get("em_rem") or 0.0
    proximity = 1.0 if em_rem <= 0 else max(0.0, 1.0 - abs(diff) / max(em_rem, 1e-6))
    strength = max(0.0, min(1.0, m.get("pin_conc", 0.0) * proximity))
    return {"direction": direction, "strength": strength,
            "note": f"max_pain {mp} vs spot {spot} (pin_conc {m.get('pin_conc')})"}


def s5_wall(snap: dict, m: dict, p) -> dict:
    spot = m["spot"]
    per = m.get("_per_doi", {})
    rw, sw = m.get("resistance_wall"), m.get("support_wall")
    direction, strength, notes = NEUTRAL, 0.0, []
    if rw is not None:
        dce_rw = per.get(rw, (0.0, 0.0))[0]
        if spot >= rw and dce_rw <= 0:
            direction, strength = BULL, 0.5; notes.append(f"resistance {rw} broken")
        elif dce_rw > 0:
            direction, strength = BEAR, 0.4; notes.append(f"CE wall building {rw}")
    if sw is not None and direction == NEUTRAL:
        dpe_sw = per.get(sw, (0.0, 0.0))[1]
        if spot <= sw and dpe_sw <= 0:
            direction, strength = BEAR, 0.5; notes.append(f"support {sw} broken")
        elif dpe_sw > 0:
            direction, strength = BULL, 0.4; notes.append(f"PE wall building {sw}")
    return {"direction": direction, "strength": strength, "note": "; ".join(notes) or "no wall event"}


def s6_iv_regime(snap: dict, m: dict, p) -> dict:
    if m.get("iv_expansion"):
        return {"direction": NEUTRAL, "strength": 0.7, "note": "IV expansion"}
    if m.get("iv_crush"):
        return {"direction": NEUTRAL, "strength": 0.7, "note": "IV crush"}
    return {"direction": NEUTRAL, "strength": 0.0, "note": "IV stable"}


def s7_straddle_trend(snap: dict, m: dict, p) -> dict:
    slope = m.get("straddle_slope", 0.0) or 0.0
    label = "expanding" if slope > 0 else "contracting" if slope < 0 else "flat"
    return {"direction": NEUTRAL, "strength": _sat(slope, 5.0), "note": f"straddle {label}"}


def compute_atoms(snap: dict, m: dict, p) -> dict:
    return {
        "oi_buildup": s1_oi_buildup(snap, m, p),
        "pcr_slope": s2_pcr_slope(snap, m, p),
        "momentum": s3_momentum(snap, m, p),
        "maxpain_pull": s4_maxpain_pull(snap, m, p),
        "wall": s5_wall(snap, m, p),
        "iv_regime": s6_iv_regime(snap, m, p),
        "straddle_trend": s7_straddle_trend(snap, m, p),
    }
