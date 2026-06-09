# Mechanical Signal System — Specification ("the Oracle")
### Nifty 50 / Bank Nifty · option-chain positioning + ICT structure

> This document is the **single source of truth** for the signal engine. The live engine and
> the backtest engine must both produce outputs that match this spec exactly — if behaviour
> and spec disagree, one of them is a bug. Everything here is **deterministic and pure**:
> given the same inputs, the same `Stance` comes out, every time.

---

## 0. Principles

1. **Pure functions.** Every signal and layer is `f(inputs) → output` with no hidden state.
   All state (history buffers, baselines) is passed in explicitly.
2. **Closed-bar only.** Chain signals act on a *completed* 60s snapshot; structure signals act
   on a *closed* spot bar (1m/5m). Never act on an in-progress interval.
3. **One code path.** Live and backtest call the identical functions; only the data source
   differs (live feed vs recorded snapshots).
4. **Honest inputs.** Where an input rests on an unobservable assumption (dealer positioning),
   it is flagged, made optional, and given an override. The Oracle never launders a guess into
   a fact. See §10.
5. **The output is a `Stance`, not a tip.** The Oracle decides *posture* (trade / fade / wait /
   stand aside) and *why*. Translating a trade-posture into a specific strike, target and SL is
   a **downstream** trade-construction layer (your existing greeks/EV cards), not the Oracle.

---

## 1. Data contracts

### 1.1 `ChainSnapshot` (one per underlying per 60s)
```
ChainSnapshot {
  underlying            # "NIFTY" | "BANKNIFTY"
  expiry                # date
  ts_ist                # snapshot close time
  spot                  # underlying value from the chain payload
  strikes[] {
    strike
    ce { oi, oi_day_open, ltp, iv, volume, delta, gamma, theta, vega }
    pe { oi, oi_day_open, ltp, iv, volume, delta, gamma, theta, vega }
  }
}
```

### 1.2 `SpotBar` (from the websocket feed, the new sub-60s source)
```
SpotBar { underlying, ts_ist, tf ("1m"|"5m"), open, high, low, close, volume }   # volume from futures proxy
```

### 1.3 `DerivedMetrics` (computed from one snapshot + history) — see §2.

### 1.4 `Stance` (the Oracle's only public output)
```
Stance {
  underlying, ts_ist,
  regime        ∈ {PINNED, TRENDING, NEUTRAL},   regime_confidence ∈ [0,1]
  direction     ∈ {BULL, BEAR, NEUTRAL},          direction_score ∈ [-1,1], conviction ∈ [0,1]
  action        ∈ {TRADE_WITH_BIAS, FADE_TO_PIN, WAIT_FOR_TRIGGER, STAND_ASIDE}
  side          ∈ {LONG, SHORT, NONE}            # option direction implied (CE/PE chosen downstream)
  entry_trigger # null | {type, level, condition}     (from structure layer)
  invalidation  # null | price
  size_factor   ∈ [0,1]                            # 0 ⇒ no trade
  why[]         # ordered, human-readable reasons (regime, direction, structure)
  inputs_ref    # hash/id of the metrics snapshot that produced this (audit + backtest)
}
```

---

## 2. Metric layer (formulas)

All sums are over listed strikes. `S` = spot. `ATM` = strike nearest `S`.
`Δoi(k) = oi(k) − oi_day_open(k)` (today's build). Slope uses the last `N_slope` snapshots.

- **PCR (OI):** `pcr_oi = ΣPE.oi / ΣCE.oi`
- **PCR (ΔOI):** `pcr_doi = ΣΔPE.oi / ΣΔCE.oi`  *(undefined if denominator ≤ 0 → flag, don't divide)*
- **Max pain:** `argmin_K  [ Σ_k CE.oi(k)·max(K−k,0) + Σ_k PE.oi(k)·max(k−K,0) ]`
  (the settlement strike that pays option holders the least).
- **ATM straddle:** `straddle = CE.ltp(ATM) + PE.ltp(ATM)`
- **Expected move (full session):** `EM_full ≈ straddle`  *(rule of thumb; cross-check with
  `S · iv_atm · √(T_session_years)`)*.
  **Remaining EM:** `EM_rem = EM_full · √(minutes_left / session_minutes)`.
- **IV baseline:** `iv_base = EMA(iv_atm, span = N_iv)` over the session.
- **Walls:** `resistance_wall = argmax_{k>S} CE.oi(k)`, `support_wall = argmax_{k<S} PE.oi(k)`.
- **Gamma pin:** `pin = argmax_k [ gamma(k)·(CE.oi(k)+PE.oi(k)) ]`.
- **Pin concentration:** `pin_conc = gamma_oi(pin) / Σ_k gamma_oi(k)`  ∈ (0,1] — how magnetic the pin is.
- **VWAP (intraday):** computed on the **futures** series (index has no volume); `vwap = Σ(price·vol)/Σvol`.
- **Realized vol (intraday):** stdev of `SpotBar` log-returns, annualised → `rv`. Compare to `iv_atm`.
- **Net ΔOI:** `dCE = ΣΔCE.oi`, `dPE = ΣΔPE.oi` (in contracts).

> Display note: keep OI/ΔOI in **contracts** internally. Convert to ₹ notional only for display,
> and never mix scopes (full-chain vs ±N-strike) under one label.

---

## 3. Atomic signal catalog

Each signal: `f(metrics, history) → { direction ∈ {BULL,BEAR,NEUTRAL}, strength ∈ [0,1] }`.
Defaults are **starting points to tune** (§7, §9).

| # | Signal | Rule (summary) | Output |
|---|--------|----------------|--------|
| 1 | **OI buildup** | Per zone, classify `Δoi` vs price-since-ref: price↑&OI↑ long-build (BULL); price↓&OI↑ short-build (BEAR); price↓&OI↓ long-unwind; price↑&OI↓ short-cover (BULL). Fresh CE writing above spot ⇒ BEAR; fresh PE writing below spot ⇒ BULL. **Writing is judged by Δoi sign, never by premium.** | dir + strength = norm(|Δoi|) |
| 2 | **PCR slope** | Primary = sign of `pcr_doi` slope over `N_slope` (rising ⇒ put writing ⇒ BULL). Level is context; flag `extreme` if `pcr_oi`>`pcr_hi` or <`pcr_lo` (contrarian-reversal context, not a trigger). | dir from slope, strength from magnitude |
| 3 | **Momentum** | `S` vs `day_open` and vs `vwap`. Both above ⇒ BULL, both below ⇒ BEAR, split ⇒ NEUTRAL. | dir + strength = norm(distance) |
| 4 | **Max-pain pull** | `dir = sign(max_pain − S)` (price drawn to pain). Strength scales with `pin_conc` and inversely with `|S−max_pain|/EM_rem`. **Only weighted inside PINNED regime.** | dir + strength |
| 5 | **Wall break/build** | `S` closes beyond `resistance_wall`+buf with wall OI not growing ⇒ BULL (resistance broken). Fresh growing CE wall above ⇒ BEAR (cap building). Mirror for PE/support. | dir + strength |
| 6 | **IV regime** | `iv_atm` vs `iv_base`: >×(1+α) ⇒ expansion; <×(1−α) ⇒ crush. **Non-directional** — feeds regime + a vol flag. | NEUTRAL + strength |
| 7 | **Straddle/EM trend** | `straddle` slope: expanding ⇒ expansion; contracting ⇒ compression. **Non-directional** — feeds regime. | NEUTRAL + strength |

Signals 1–5 are directional (feed the Direction engine). Signals 4 is gated by regime.
Signals 6–7 are regime inputs only.

---

## 4. Layer engines

### 4.1 Regime gate — `classify_regime(metrics, history) → {regime, regime_confidence}`
**Built only on defensible, observable inputs.** (Dealer-sign GEX is an optional overlay, §10.)

Score each axis to [0,1] toward "pinned" vs "trending":
- `near_pin = clamp(1 − |S − pin|/(0.5·EM_rem), 0, 1)`            (closer to pin ⇒ pinned)
- `pinned_vol = clamp((iv_base − iv_atm)/iv_base ·k1 + (1 − rv/iv_atm)·k2, 0, 1)`  (falling IV, realized < implied ⇒ pinned)
- `compression = straddle contracting ? 1 : 0`
- `magnet = pin_conc`                                              (concentration ⇒ pinned)

```
pinned_score   = w·[near_pin, pinned_vol, compression, magnet]
trending_score = w·[1−near_pin, 1−pinned_vol, expansion, iv_spike]

regime = PINNED   if pinned_score   ≥ θ_pin   and pinned_score   > trending_score
         TRENDING if trending_score ≥ θ_trend and trending_score > pinned_score
         NEUTRAL  otherwise
regime_confidence = |pinned_score − trending_score|
```
**Hysteresis:** enter PINNED at `near_pin > 0.7`, exit only below `0.55` (prevents flip-flop).
**Override:** a confirmed structure break through the pin (§4.3) forces TRENDING for `T_break`.

### 4.2 Direction engine — `aggregate_direction(signals) → {direction, score, conviction}`
```
s_i ∈ [-1,1]  (BULL=+strength, BEAR=−strength, NEUTRAL=0), only directional signals 1–5
score = Σ w_i·s_i / Σ w_i
agree = (#signals with sign == sign(score)) ; disagree = (#opposite)
alignment = max(0, (agree − disagree)/total_directional)
conviction = clamp(|score| · alignment, 0, 1)
direction = BULL if score > +ε ; BEAR if score < −ε ; else NEUTRAL
```

### 4.3 Structure layer — `read_structure(spot_bars) → structure` (operates on the **ws feed**)
Mechanical ICT definitions (no discretion):
- **Swing pivot:** `k`-bar fractal high/low.
- **BSL/SSL:** most recent swing high / low (liquidity pools).
- **Sweep:** bar wicks beyond a swing point then **closes back inside** within `N_sweep` bars.
- **MSS:** a **close** beyond the most recent opposing swing ⇒ new `last_mss_dir`.
- **Order block:** last opposing candle before the displacement that caused the MSS; zone = its range.
- **FVG:** 3-bar gap (`bar1.high < bar3.low` bullish; mirror bearish).
- **Premium/discount:** position of `S` within current dealing range (>50% premium, <50% discount).

```
structure = {
  last_mss_dir, recent_sweep?, in_zone (premium|discount|equilibrium),
  nearest_ob, nearest_fvg,
  entry_trigger:  e.g. "5m close beyond <level> in bias direction after sweep"
  invalidation:   beyond the OB / swept liquidity
  agreement:      AGREE | DISAGREE | NEUTRAL   (vs Direction engine side)
}
```
Structure **never votes on direction** — it supplies trigger, invalidation, and an agreement flag.

---

## 5. Reconciliation — `reconcile(regime, direction, structure, guards) → Stance`

The decision matrix (regime gates, direction sides, structure triggers & sizes):

| Regime → / Direction ↓ | Strong (conviction ≥ θ_conv) | Weak (< θ_conv) |
|---|---|---|
| **PINNED** | `FADE_TO_PIN` toward pin (a strong read into the pin is writers defending it); never chase the breakout. Size reduced. | `STAND_ASIDE` (directional). Range/premium context only. |
| **TRENDING** | `TRADE_WITH_BIAS`, side = direction, entry = structure trigger, full size if structure AGREES. | `WAIT_FOR_TRIGGER`, small size. |
| **NEUTRAL** | `WAIT_FOR_TRIGGER`, require structure AGREE, half size. | `STAND_ASIDE`. |

```
size_factor = base · regime_confidence · conviction · struct_factor
  struct_factor = 1.0 if AGREE, 0.5 if NEUTRAL, 0.0 if DISAGREE
  size_factor = 0 in every STAND_ASIDE / WAIT_FOR_TRIGGER cell
```
**Conflict rules (hard):**
- Direction and structure **disagree on side** ⇒ `WAIT_FOR_TRIGGER`, never trade.
- PINNED + strong directional **against the pin** ⇒ `FADE_TO_PIN` or `STAND_ASIDE` — the Oracle
  will **refuse** a directional trade whose invalidation sits on the pin.
- `conviction < θ_floor` ⇒ `STAND_ASIDE` regardless of regime.

**Worked example (the reviewed live state):** regime=PINNED (spot on 23200 pin, falling IV,
straddle compressing), direction=BEAR weak (conviction 0.30), structure=NEUTRAL/stand-aside.
→ PINNED × Weak ⇒ **STAND_ASIDE**, `size_factor = 0`, and the 23200 PE (stop on the pin) is
explicitly refused. `why = [pinned regime, weak/partial bias, no structure trigger]`.

---

## 6. Guardrails & production semantics

- **Freshness gate:** snapshot age > `90s` OR ws-spot stale ⇒ emit `STAND_ASIDE` with reason
  `data_stale`; do not compute a tradeable stance on stale inputs.
- **Cooldown:** no new stance flip for the same `(underlying, action)` within `T_cool` unless
  conviction jumps by `Δconv_min`.
- **Dedup:** `dedup_key = hash(regime, direction, round(trigger_level))`; suppress repeats.
- **Confidence floor:** §5 `θ_floor`.
- **Time-of-day:** in the last hour, raise pin/max-pain weights (pinning intensifies into
  expiry); block **new** 0DTE directional entries after `cutoff` (e.g. 15:05 IST).
- **Regime hysteresis:** §4.1.

---

## 7. Calibration & validation

- **Dataset:** forward-recorded snapshots (Risk R1 — recording starts in master Phase 1).
- **Tuning:** fit weights/thresholds on in-sample, validate **out-of-sample**; walk-forward.
- **Separate parameter sets for Nifty and Bank Nifty** (Bank Nifty is more volatile).
- **Metrics:** stance hit-rate, forward return by horizon conditional on `action`, false-trigger
  rate, regime-classification stability (flip count/day), `STAND_ASIDE` precision (did standing
  aside avoid losers?).
- **Parity test:** replayed stance == live stance for the same snapshot (byte-identical).

---

## 8. Phase-wise distribution

Each signal-system phase maps to a master-plan phase (see the build-plan doc).

| Phase | Name | Maps to | Deliverables | Done when |
|---|---|---|---|---|
| **S-A** | Metric substrate | master Ph 2 | §2 metrics as deterministic pure functions; history buffers | max pain / PCR / EM / pin match a manual reference within tolerance; unit tests green |
| **S-B** | Atomic signals | master Ph 3 | §3 signals 1–7 as pure functions emitting `{dir,strength}`; golden-file tests | each signal reproduces hand-checked cases; writing judged by Δoi only |
| **S-C** | Layer engines | master Ph 3 | §4 regime gate, direction engine, structure adapter (on ws bars) | regime stable under hysteresis; direction conviction reflects agreement; structure emits trigger+invalidation |
| **S-D** | Reconciliation | master Ph 3 | §5 combiner + `Stance` + §6 guardrails | reproduces the worked example (PINNED×weak ⇒ STAND_ASIDE, PE refused); never double-fires |
| **S-E** | Calibration | master Ph 4 | §7 backtest harness, per-index tuning, OOS validation | live==backtest parity; tuned params documented; metrics reported |
| **S-F** | Production semantics | master Ph 5–6 | freshness, cooldown, dedup, time-of-day, monitoring of stance latency & regime flips | unattended session produces auditable stances; alerts on stale data |

**Sequencing rule:** S-A→S-D are pure logic and can be built/tested *before* you have much data
(synthetic + hand cases). S-E is the one phase gated on the recorded dataset — so start S-A…S-D
in parallel with Phase-1 recording, and S-E begins once enough sessions are banked.

---

## 9. Open parameters (all tunables, with starting defaults)

| Param | Meaning | Default |
|---|---|---|
| `N_slope` | snapshots for PCR/straddle slope | 5 (≈5 min) |
| `N_iv` | EMA span for IV baseline | 20 |
| `pcr_hi / pcr_lo` | PCR extreme levels | 1.3 / 0.7 |
| `α` | IV spike/crush band | 0.10 |
| `θ_pin / θ_trend` | regime entry scores | 0.55 / 0.55 |
| `θ_conv` | strong-conviction threshold | 0.55 |
| `θ_floor` | stand-aside conviction floor | 0.25 |
| `w_i` | direction signal weights | equal, then tuned |
| `k`, `N_sweep` | swing fractal, sweep window | 2, 3 bars |
| `T_cool`, `Δconv_min` | cooldown, override jump | 5 min, 0.2 |
| `cutoff` | no new 0DTE directional entries | 15:05 IST |

All defaults are placeholders to be replaced by §7 calibration. **Do not ship the defaults as truth.**

---

## 10. Known limitations & honesty

- **Dealer-gamma sign is not observable** from anonymous NSE OI. The regime gate therefore uses
  pin concentration + vol behaviour (observable), and treats any "dealers long/short gamma" label
  as an **optional, low-confidence overlay** with a live INVERT flag. Never present it as fact.
- **ICT is inherently subjective** — the §4.3 definitions are *one* mechanisation; they will have
  false positives. Backtest them like any other signal; drop the ones that don't earn their place.
- **0DTE sensitivity:** on expiry day, theta and pin effects dominate and the 60s cadence is a real
  limitation; the time-of-day guardrails (§6) exist for this reason.
- **No edge guarantee.** The Oracle enforces *discipline and consistency*, not profitability. Only
  §7 validation tells you which configurations have positive expectancy — separately per index.
