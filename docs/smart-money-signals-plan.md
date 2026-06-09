# Smart Money — Mechanical Signals System
## Systematic Build Plan (Nifty 50 & Bank Nifty)

> Scope locked to NSE index options. Gold and crypto are deferred — the gateway's
> adapter layer leaves the door open for them later without a rewrite.

---

## 1. Objective

The live app (build v74) already polls the chain every 60s and runs three engines — an ICT
price-action layer, an OI/gamma positioning engine, and a verdict + trade-recommendation
engine — for **Nifty 50**. The goal is therefore *not* to add polling; it is to fix the
structural gaps the UI review exposed (see §1.5), **reconcile the three engines into one
mechanical stance** (see §5.5), move ingestion + recording to an always-on backend so a
backtestable dataset accumulates, and extend cleanly to **Bank Nifty**.

**In scope**
- 60s option-chain polling for Nifty 50 + Bank Nifty, market-hours aware
- Derived positioning metrics (PCR, max pain, OI walls, IV baseline, straddle)
- A mechanical signal engine over those metrics, with guardrails
- A backtest harness that shares the live signal code
- UI integration into the existing Next.js app

**Out of scope (deferred)**
- Gold (XAU/USD or MCX) and crypto
- Order placement / execution (this is an *analytics + signal* system, not an auto-trader)

---

## 1.5 Current-state findings (from UI review, build v74)

The live app is genuinely sophisticated — per-strike greeks cards, an IV-derived touch-
probability EV model, a gamma pin/regime read, and a plain-language OI footprint. The gaps
below are what block the move to reliable mechanical signals:

- **Stale spot drives precise triggers.** Everything runs off the 60s pull; even "live spot"
  is cached ~59s. Yet the entry rule is "enter only if spot breaks below X, wait for a 5-min
  candle close." A once-a-minute spot can't reliably detect a break or a candle close →
  **needs a separate websocket spot feed.**
- **0DTE on minute-old premiums.** Same-day-expiry recommendations (theta ≈ −₹135/hr/lot)
  are quoted from a snapshot "1 min ago · premiums likely current." On 0DTE that staleness is
  material; the trade math is paisa-precise on top of it.
- **Engines contradict and aren't reconciled.** ICT said "no clear trend, stand aside" while
  the OI engine said "bearish, buy PE," while the gamma read said "pinned, mean-revert" — and
  the recommended PE's stop sat *on* the gamma pin / heaviest call wall. Conviction was only
  −1.5 yet a full trade card was issued. **This is the core fix — see §5.5.**
- **Attribution shortcut.** "Premium falling confirms writing" conflates writing with delta,
  theta and IV effects; ΔOI in contracts is the real writing signal, not premium direction.
- **EV conflates units.** Headline EV is in index points from two non-independent touch
  probabilities, while actual P&L is in premium and nonlinear in spot (delta/gamma).
- **Collection depends on a browser tab.** Auto-fetch appears to run client-side, so nothing
  is recorded when the tab is closed — no server history, no accumulating backtest dataset.
  **This is Risk R1 made concrete; the always-on backend is the fix.**
- **Inconsistent aggregation scope** (full-chain vs ±10-strike ΔOI shown as the same label),
  and OI displayed in ₹Cr notional folds premium into the position measure.
- **Compliance flag (not legal advice).** Specific buy/target/SL calls plus a private admin
  recommendation layer is the territory SEBI's RA/IA rules scrutinize despite the disclaimer;
  confirm with someone qualified before distributing to other users.

---

## 2. India-specific constraints (these shape every decision)

1. **Market hours.** Equity F&O trades **09:15–15:30 IST** on NSE trading days, with a
   pre-open 09:00–09:15. The poller must be gated by a **live NSE trading-day calendar**
   (holidays + occasional special/half sessions), not a naive clock.
2. **Option chain is poll-based, OI-centric.** The full chain (OI, ΔOI, IV, Greeks per
   strike) comes from a **REST snapshot endpoint**, not a tick stream. A 60s cadence sits
   comfortably inside broker rate limits (e.g. Dhan allows one chain request per underlying
   every 3 seconds). Underlying **spot** can come over a websocket for faster price context.
3. **Expiry & lot size are moving targets.** SEBI/NSE have repeatedly changed expiry days
   and lot sizes (Nifty weekly shifted to **Tuesday** in the Sept-2025 reshuffle; index lot
   sizes were rebased in Jan-2026). **Never hardcode these** — fetch the expiry list and lot
   size from the broker/exchange at startup and on a daily refresh.
4. **Broker tokens expire daily.** Most Indian broker APIs (Zerodha, Dhan, Upstox, Fyers,
   Angel) issue an access token that expires each day and often needs a TOTP login. The
   backend must handle **automated daily token refresh** as a first-class concern.
5. **Vercel is UI-only.** Serverless functions are short-lived and stateless — they cannot
   hold a websocket open or keep indicator state between ticks. The ingestion + signal
   engine must run as an **always-on backend process**; Vercel keeps the frontend.

---

## 3. Target architecture

```
NSE option chain (via broker/vendor)
        │  60s REST poll (per underlying, market-hours gated)
        ▼
  Market-data gateway  ──► canonical ChainSnapshot event
        │
        ▼
  Message bus  (Redis Streams)
        │
        ▼
  Metrics + signal workers  ◄──►  Redis (hot state, dedup, latest snapshot)
        │
        ├──► Postgres + TimescaleDB  (snapshots, signals, audit)
        └──► Push to UI  (SSE / websocket)
                  │
                  ▼
           Next.js app on Vercel  (UI only)
```

**Component responsibilities**

| Component | Responsibility |
|---|---|
| Instrument registry | Live lot size, strike step, expiry list per underlying (Nifty, Bank Nifty) |
| Gateway / poller | Market-hours-gated 60s poll, retries, gap handling, daily token refresh, normalize to `ChainSnapshot` |
| Metrics workers | Per-snapshot derived metrics (PCR, max pain, OI walls, IV baseline, straddle) |
| Signal workers | Apply the signal catalog as pure functions; guardrails (cooldown, dedup, one-per-close) |
| Storage | Timescale for time-series snapshots/signals/audit; Redis for hot state + dedup keys |
| UI bridge | Backend pushes live snapshot + signals to the Next.js frontend |

---

## 4. Canonical data model

**InstrumentRegistry** (refreshed daily, never hardcoded)
- `underlying`, `lot_size`, `strike_step`, `expiry_list[]`, `current_weekly_expiry`, `monthly_expiry`

**ChainSnapshot** (one per underlying per 60s tick)
- `underlying`, `expiry`, `ts_ist`, `spot`
- per strike: `{ strike, CE:{ltp, oi, oi_change, iv, volume, greeks}, PE:{...} }`

**DerivedMetrics** (computed from a snapshot + recent history)
- `pcr_oi`, `pcr_volume`, `max_pain`, `total_ce_oi`, `total_pe_oi`
- `atm_straddle`, `iv_baseline`, `top_oi_strikes_ce[]`, `top_oi_strikes_pe[]`

**Signal**
- `id`, `underlying`, `ts_ist`, `type`, `direction` (bullish/bearish/neutral), `strength`
- `inputs` (snapshot of the metrics that triggered it — for audit/backtest)
- `dedup_key`, `cooldown_until`

---

## 5. Mechanical signal catalog (the prop-desk layer)

Each signal is a **pure function of `DerivedMetrics` + recent history**, so the same code
runs live and in backtest. Parameters below are **starting points to be tuned via backtest**,
not gospel.

1. **OI buildup classification** — for the aggregate and for the top strikes, classify the
   price-vs-ΔOI quadrant: price↑ + OI↑ = *long buildup* (bullish), price↓ + OI↑ = *short
   buildup* (bearish), price↓ + OI↓ = *long unwinding*, price↑ + OI↓ = *short covering*
   (bullish).
2. **PCR level + slope** — OI PCR above/below thresholds (e.g. >1.3 / <0.7) plus its slope
   over the last N snapshots; rising PCR = put writing = support building.
3. **Max pain & max-pain shift** — distance of spot from max pain, and intraday drift of max
   pain (pull toward expiry).
4. **OI-wall support/resistance** — highest PE-OI strike = support, highest CE-OI strike =
   resistance; flag breaches and fresh wall formation.
5. **IV spike / crush** — current ATM IV vs a rolling intraday baseline; spikes flag event
   risk, crush flags premium decay.
6. **ΔOI leaders** — top strikes by change in OI this interval (where fresh positions are
   being added).
7. **ATM straddle trend** — straddle price as the market's expected move; expanding vs
   contracting.

Each emits `{direction, strength}` and is gated by **cooldown + dedup** so it can't refire
every 60s on the same condition. A simple **composite score** can aggregate them later.

> Honest note: these are *signals*, not edge guarantees. Their value is decided by the
> backtest in Phase 4, separately for Nifty vs Bank Nifty (Bank Nifty is more volatile and
> will likely need different parameters).

---

## 5.5 Signal reconciliation engine (layered, not a vote)

The current app's flaw is averaging three engines into one signed score. The fix is to treat
them as **layers with different roles**, resolved by rule — not as equal voters.

**Layer 1 — Regime gate** (gamma sign · distance to pin · IV). Classifies the session as
*pinned* (long-gamma → fade moves, mean-revert toward pin), *trending* (short-gamma → moves
extend) or *neutral*. This **gates which playbook is allowed**, it does not pick a side.
*Caveat:* dealer-gamma sign is inferred in India (hence the app's INVERT toggle) — treat
regime as a hypothesis that an IV expansion or a structure break can override.

**Layer 2 — Direction** (ΔOI footprint by zone · PCR slope · price vs day-open/VWAP ·
max-pain pull). Emits a signed bias whose conviction = how many sub-signals agree. Uses ΔOI
in **contracts** and distinguishes writing from unwinding via OI change, never via premium.

**Layer 3 — Structure & timing** (ICT). Does **not** vote on direction. Supplies the entry
trigger (MSS / sweep-and-reclaim in the bias direction), the invalidation level (beyond the
OB or swept liquidity), and an agree/disagree flag.

**Combiner rule:** regime gates the playbook · direction sets the side · structure sets entry
and stop · conviction (hence size) = agreement across layers · any genuine conflict shrinks
size or stands aside, never chases.

| Regime → / Direction ↓ | Strong read | Weak read |
|---|---|---|
| **Pinned** | Fade toward pin / sell premium — a strong read into the pin is dealers defending it; do NOT buy the breakout | Range/neutral; stand aside directionally |
| **Trending** | Trade *with* it; structure is the trigger; targets can run | Wait for a structure trigger; small size |
| **Neutral** | Require structure confirmation; half size | Stand aside |

**Worked example (the reviewed screenshot):** regime = *pinned* (spot on the 23200 pin,
"sell rips/buy dips," max pain at spot), direction = *weak bearish* (−1.5, partial
footprint), structure = *stand aside*. → **STAND ASIDE**, and the engine explicitly refuses
the 23200 PE whose stop sits on the pin. The app issued a full PE instead — that delta is the
value of reconciliation.

**Output contract (one auditable object):**
```
Stance {
  regime, direction, conviction(0-1),
  action ∈ {trade_with_bias, fade_to_pin, wait_for_trigger, stand_aside},
  entry_trigger, invalidation, size_factor, why[]
}
```

---

## 6. Phased delivery

### Phase 0 — Foundations
**Goal:** backend scaffold the rest hangs off.
**Deliverables:** always-on backend service; instrument registry pulling live lot/expiry;
NSE trading-day + holiday calendar; canonical schemas; repo + CI; secrets + daily
token-refresh job.
**Done when:** the service starts, knows whether the market is open, and prints the correct
live lot size + current expiry for both indices.

### Phase 1 — 60s option-chain poller
**Goal:** replace four snapshots with continuous polling.
**Deliverables:** gateway adapter for the chosen data source; market-hours-gated 60s loop
aligned to the wall-clock minute; retries + gap handling; dedup; persistence of every
`ChainSnapshot` for Nifty + Bank Nifty.
**Done when:** a full session produces ~375 clean snapshots per index, no duplicates, and the
poller survives a mid-session token refresh and a brief network drop.

### Phase 2 — Derived metrics
**Goal:** turn raw chains into positioning metrics.
**Deliverables:** metrics workers computing PCR, max pain, OI walls, IV baseline, straddle
per snapshot; stored alongside snapshots.
**Done when:** metrics match a manual spot-check against a reference chain (e.g. Sensibull /
NiftyTrader) within tolerance.

### Phase 3 — Signal engine + reconciliation
**Goal:** the catalog (§5) plus the layered stance engine (§5.5), live.
**Deliverables:** each signal as a pure function; the three-layer combiner emitting a single
`Stance` object; guardrails (cooldown, dedup, session filter, act-only-on-closed-interval);
persist + emit. The `Stance` replaces the current lone score.
**Done when:** the engine reproduces the worked example (pinned + weak → stand aside, PE
refused), never double-fires, and every stance stores the layer inputs and `why[]`.

### Phase 4 — Backtest harness
**Goal:** know which signals actually work.
**Deliverables:** replay recorded snapshots through the *same* signal code; per-signal
metrics (hit rate, forward return by horizon) for Nifty and Bank Nifty separately.
**Done when:** a replayed signal is byte-identical to the live signal for the same snapshot.
**⚠ Critical dependency — see Risk R1:** this needs a historical snapshot dataset.

### Phase 5 — UI integration
**Goal:** surface it in the existing app.
**Deliverables:** backend → frontend push (SSE/websocket); a single `Stance` panel that
replaces the contradictory parallel reads; live signal feed; per-strike OI heatmap; index
toggle (Nifty / Bank Nifty). Existing greeks/EV/footprint views are kept but fed by the
backend.
**Done when:** the app shows live chains and signals updating each minute during market hours.

### Phase 6 — Observability & hardening
**Goal:** production trust.
**Deliverables:** data-freshness SLA (alert if no snapshot within ~90s during market hours);
signal-latency metric; token-refresh-failure alert; dead-letter handling; half-day/holiday
handling; soak test across a full session.
**Done when:** the system runs an unattended session and you'd believe its output the next
morning.

---

## 7. Recommended stack

- **Backend:** Python + asyncio for the poller and signal engine (numpy/pandas for metrics;
  the 60s budget makes sub-millisecond compute trivially achievable).
- **Storage:** Postgres + TimescaleDB (snapshots, signals, audit); Redis (hot state, dedup
  keys, latest-snapshot cache).
- **Bus:** Redis Streams to start (move to Kafka only if you ever need replay + many consumer
  groups — not needed at this scale).
- **UI transport:** SSE or websocket from the backend to the Next.js app.
- **Hosting:** backend on Render / Railway / Fly.io / small VPS; **keep the UI on Vercel**.

---

## 8. Key risks & mitigations

- **R1 — Backtest data is the hard part.** Clean *historical option-chain OI snapshots* are
  scarce and usually paid. You most likely need to **start recording snapshots now** (Phase 1
  doubles as data collection) to build a forward dataset, and/or buy historical chain data.
  Plan for weeks of recording before Phase 4 is meaningful. This is the biggest schedule risk.
- **R2 — Data source reliability.** Rate limits, token expiry, occasional bad/lagged OI in a
  snapshot. Mitigate with retries, freshness checks, and validation on each snapshot.
- **R3 — Snapshot timing vs OI update lag.** OI from the chain endpoint is a snapshot and can
  lag price; treat OI signals as positioning (slower) signals, not tick-precision triggers.
- **R4 — Parameter overfitting.** Tune on one period, validate out-of-sample; keep Nifty and
  Bank Nifty parameter sets separate.
- **R5 — Compliance.** Use a broker/vendor API, not direct NSE scraping (fragile and against
  site terms). This is an analytics tool — no order placement.

---

## 9. Open decisions (your input finalizes Phase 0/1)

1. **Data source.** Broker API (cheap/free if you have an account; daily token; rate limits)
   vs paid vendor (e.g. TrueData — clean websockets, no token hassle). *Recommended default:*
   a broker with a clean documented chain API (Dhan or Fyers) since you likely already trade
   with one — and it's swappable behind the gateway adapter.
2. **Spot feed.** Websocket for faster underlying price, or just use the chain's underlying
   value? (Affects whether any signal needs sub-60s price.)
3. **Signal delivery.** In-app only, or also push alerts (e.g. Telegram)?

---

## 10. Suggested first milestone

**Phase 0 + Phase 1 = "live 60s chain for Nifty + Bank Nifty, persisted, market-hours aware."**
This kills the four-snapshot limitation, starts your backtest dataset (Risk R1), and proves
the riskiest plumbing before any signal logic is written. Everything else builds on it.
