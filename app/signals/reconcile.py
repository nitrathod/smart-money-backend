"""Reconciliation — spec §5 (decision matrix) + §6 (guardrails). Produces the Stance.

This is the only place a posture is decided. It reproduces the worked example:
PINNED × weak ⇒ STAND_ASIDE with size 0, refusing a directional trade whose stop sits on the pin.
"""

from __future__ import annotations

from datetime import datetime, time

PINNED, TRENDING, NEUTRAL_R = "PINNED", "TRENDING", "NEUTRAL"
BULL, BEAR, NEUTRAL = "BULL", "BEAR", "NEUTRAL"
AGREE, DISAGREE, NEUTRAL_S = "AGREE", "DISAGREE", "NEUTRAL"

TRADE_WITH_BIAS = "TRADE_WITH_BIAS"
FADE_TO_PIN = "FADE_TO_PIN"
WAIT_FOR_TRIGGER = "WAIT_FOR_TRIGGER"
STAND_ASIDE = "STAND_ASIDE"


def _side_from_dir(direction: str) -> str:
    return "LONG" if direction == BULL else "SHORT" if direction == BEAR else "NONE"


def _parse_hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def reconcile(m: dict, regime_out: dict, direction_out: dict, structure: dict, p,
              *, data_stale: bool, now_ist: datetime) -> dict:
    regime = regime_out["regime"]
    direction = direction_out["direction"]
    conviction = direction_out["conviction"]
    agreement = structure.get("agreement", NEUTRAL_S)
    struct_factor = {AGREE: 1.0, NEUTRAL_S: 0.5, DISAGREE: 0.0}[agreement]
    strong = conviction >= p.theta_conv

    why: list[str] = [
        f"regime={regime} (conf {regime_out['regime_confidence']}, near_pin {regime_out['near_pin']})",
        f"direction={direction} (score {direction_out['score']}, conviction {conviction})",
        f"structure={agreement} ({structure.get('quality')})",
    ]

    def stance(action, side, size, extra_why=None):
        if action in (STAND_ASIDE, WAIT_FOR_TRIGGER):
            size = 0.0
        return {
            "underlying": m["underlying"], "ts_ist": m["ts_ist"],
            "regime": regime, "regime_confidence": regime_out["regime_confidence"],
            "direction": direction, "direction_score": direction_out["score"], "conviction": conviction,
            "action": action, "side": side,
            "entry_trigger": structure.get("entry_trigger") if action in (TRADE_WITH_BIAS, WAIT_FOR_TRIGGER) else None,
            "invalidation": structure.get("invalidation") if action in (TRADE_WITH_BIAS, WAIT_FOR_TRIGGER) else None,
            "size_factor": round(max(0.0, min(1.0, size)), 3),
            "why": why + (extra_why or []),
            "inputs_ref": m.get("inputs_ref"),
        }

    # ---- guardrails first (spec §6) ----
    if data_stale:
        return stance(STAND_ASIDE, "NONE", 0.0, ["data_stale: snapshot too old"])
    if conviction < p.theta_floor:
        return stance(STAND_ASIDE, "NONE", 0.0, [f"conviction {conviction} < floor {p.theta_floor}"])
    if agreement == DISAGREE:
        # direction and structure disagree on side ⇒ never trade
        return stance(WAIT_FOR_TRIGGER, "NONE", 0.0, ["direction vs structure disagree"])

    base_size = p.base_size * regime_out["regime_confidence"] * conviction * struct_factor
    after_cutoff = now_ist.timetz().replace(tzinfo=None) >= _parse_hhmm(p.cutoff_ist)

    # ---- decision matrix (spec §5) ----
    if regime == PINNED:
        if strong:
            spot, pin = m["spot"], m["pin"]
            pin_side = "LONG" if pin > spot else "SHORT" if pin < spot else "NONE"
            extra = ["fade toward pin; not chasing breakout"]
            if _side_from_dir(direction) != pin_side and pin_side != "NONE":
                extra.append("directional read is against the pin → refused, fading to pin instead")
            return stance(FADE_TO_PIN, pin_side, base_size * 0.5, extra)
        return stance(STAND_ASIDE, "NONE", 0.0, ["pinned + weak directional"])

    if regime == TRENDING:
        if strong:
            if after_cutoff:
                return stance(STAND_ASIDE, "NONE", 0.0, [f"after {p.cutoff_ist}: no new 0DTE directional entry"])
            size = base_size if agreement == AGREE else base_size * 0.5
            return stance(TRADE_WITH_BIAS, _side_from_dir(direction), size,
                          ["trending + strong bias" + ("; structure agrees" if agreement == AGREE else "")])
        return stance(WAIT_FOR_TRIGGER, "NONE", 0.0, ["trending but weak; await trigger"])

    # NEUTRAL regime
    if strong and agreement == AGREE:
        if after_cutoff:
            return stance(STAND_ASIDE, "NONE", 0.0, [f"after {p.cutoff_ist}: no new 0DTE directional entry"])
        return stance(WAIT_FOR_TRIGGER, _side_from_dir(direction), 0.0,
                      ["neutral regime; structure agrees; await confirmed trigger"])
    return stance(STAND_ASIDE, "NONE", 0.0, ["neutral regime; no strong aligned setup"])
