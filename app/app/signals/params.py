"""Tunable parameters — spec §9.

All defaults are STARTING POINTS, to be replaced by §7 calibration on recorded data.
Nifty and Bank Nifty get separate sets (Bank Nifty is more volatile). Do not treat
these defaults as truth — they exist so the engine runs end-to-end before tuning.
"""

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Params:
    # history / baselines
    n_slope: int = 5            # snapshots for PCR/straddle slope (~5 min)
    n_iv: int = 20              # EMA span for IV baseline
    # PCR
    pcr_hi: float = 1.3
    pcr_lo: float = 0.7
    # IV band
    alpha: float = 0.10         # IV spike/crush band
    # regime
    theta_pin: float = 0.55
    theta_trend: float = 0.55
    pin_enter: float = 0.70     # near_pin to ENTER pinned (hysteresis)
    pin_exit: float = 0.55      # near_pin to EXIT pinned
    k1: float = 1.0             # pinned_vol: IV-falling weight
    k2: float = 1.0             # pinned_vol: realized<implied weight
    # direction / conviction
    theta_conv: float = 0.55    # strong-conviction threshold
    theta_floor: float = 0.25   # stand-aside conviction floor
    eps_dir: float = 0.05       # direction deadband on score
    # structure
    k_fractal: int = 2          # swing fractal half-width (bars)
    n_sweep: int = 3            # sweep close-back window (bars)
    # production
    freshness_sec: int = 90     # snapshot age beyond this -> STAND_ASIDE(data_stale)
    cutoff_ist: str = "15:05"   # no new 0DTE directional entries after this
    session_minutes: int = 375  # 09:15 -> 15:30
    base_size: float = 1.0

    # direction-signal weights (equal to start; tuned later)
    w_oi_buildup: float = 1.0
    w_pcr_slope: float = 1.0
    w_momentum: float = 1.0
    w_maxpain: float = 1.0
    w_wall: float = 1.0


NIFTY = Params()
# Bank Nifty: wider bands, slightly higher conviction bar (more volatile / noisier).
BANKNIFTY = replace(NIFTY, pcr_hi=1.35, pcr_lo=0.65, alpha=0.12, theta_conv=0.60)

_BY_UNDERLYING = {"NIFTY": NIFTY, "BANKNIFTY": BANKNIFTY}


def params_for(underlying: str) -> Params:
    return _BY_UNDERLYING.get(underlying.upper(), NIFTY)
