# Smart Money — Mechanical Signals · Project Brief

A single-page map of the project. Detail lives in the linked documents; this is the index.

---

## 1. What we're building
An upgrade of the existing **Smart Money** NIFTY option-chain app into a continuous,
**60-second mechanical signal system** for **Nifty 50** (then **Bank Nifty**). It turns
option-chain positioning + price structure into **one reconciled trading stance**, runs on an
**always-on backend** that records every snapshot (so the system is backtestable), and surfaces
the result in the existing Vercel UI.

**Scope:** NSE index options only. Gold/crypto deferred (the architecture leaves the door open).
**Not in scope:** order execution — this is an analytics + signal system, not an auto-trader.

## 2. The problem it fixes (from the live-app review)
The current app already polls at 60s and runs three engines, but: data is collected **client-side**
(nothing recorded when the tab is closed), **spot is ~60s stale** yet drives precise entry triggers,
the three engines **contradict each other** with no reconciliation, and there's **no backtest dataset**.
Full findings: see the plan doc §1.5.

## 3. Architecture (one line)
**Vercel** (UI, unchanged for now) ⟶ reads from ⟶ **always-on backend on an Oracle Free VM**
(60s chain poll + websocket spot ⟶ metric/signal/stance engine ⟶ TimescaleDB ⟶ push to UI).
Diagram + rationale: plan doc §3.

## 4. The signal system
A **layered reconciliation engine**, not a vote: a **regime gate** (gamma/IV) decides the playbook,
a **direction engine** (ΔOI, PCR, momentum) sets the side, an **ICT structure layer** sets timing and
invalidation — combined into a single `Stance { regime, direction, conviction, action, size, why }`.
Full formulas and thresholds: the Oracle spec.

## 5. Deliverables (the index)
| Artifact | Purpose |
|---|---|
| `smart-money-signals-plan.md` | Build roadmap: architecture, phases 0–6, risks, deployment |
| `mechanical-signal-system-spec.md` | "The Oracle" — exact signal logic, formulas, reconciliation, params |
| `smart-money-backend/` (zip) | Phase-0 deployable backend scaffold (runs on a mock provider) |
| In-chat visuals | Architecture diagram, reconciliation diagram, redesigned-page mockup |

## 6. Tech stack
- **Backend:** Python 3.12 · FastAPI · APScheduler · asyncpg · httpx (Docker)
- **Storage:** PostgreSQL + TimescaleDB (snapshots, stances) — self-hosted on the VM
- **Host:** Oracle Cloud Always Free VM (Ampere ARM, Mumbai/Hyderabad) — genuinely always-on
- **Frontend:** Next.js on Vercel (existing)
- **Code/CI:** GitHub · **Monitoring:** UptimeRobot · **Data:** Dhan/Fyers broker API

## 7. Phases & status
Master phases map to signal-system phases S-A…S-F (Oracle spec §8).

| Phase | What | Status |
|---|---|---|
| 0 — Foundations | Backend scaffold, market-hours gate, schema, token-refresh hook | ✅ built (runs on mock); real-broker wiring pending |
| 1 — 60s poller + recording | Real chain poll for Nifty + Bank Nifty; dataset starts accumulating | 🟡 scaffold polls & persists mock; needs Dhan provider |
| 2 — Derived metrics (S-A) | PCR, max pain, EM, gamma pin, walls | ⬜ not started |
| 3 — Signals + reconciliation (S-B…S-D) | Atomic signals, layer engines, `Stance` | ⬜ not started (pure logic — can begin in parallel) |
| 4 — Backtest + calibration (S-E) | Replay harness, per-index tuning | ⬜ gated on recorded data (Risk R1) |
| 5 — UI integration | `Stance` panel, backend→UI push, HTTPS | ⬜ not started |
| 6 — Observability + hardening (S-F) | Freshness SLA, cooldown, monitoring | ⬜ not started |

## 8. Key decisions made
- Markets: **Nifty 50 first**, then Bank Nifty. · Data: **broker API (Dhan default)**, swappable.
- Cadence: 60s chain poll + **websocket spot** for sub-minute triggers.
- Host: **Oracle Free VM** (most free PaaS sleep; a real VM doesn't). · Redis deferred (one process is enough at this scale).

## 9. Risks
- **R1 (biggest):** historical option-chain OI data is scarce — backtesting can't start until the
  poller has recorded enough sessions. Phase 1 doubles as data collection; start it early.
- Broker token expires daily → automated refresh. · GEX dealer-sign is **unobservable** on NSE → regime gate avoids relying on it. · ICT signals are subjective → must earn their place in backtest. · Specific buy/SL calls touch SEBI RA/IA rules → confirm with a qualified person before distributing. · 0DTE + 60s cadence is a real latency limit → time-of-day guardrails.

## 10. Immediate next steps
1. **Deploy Phase 0 on mock** → confirm `/healthz` green and snapshots saving.
2. **Wire `app/providers/dhan.py`** (`fetch_chain` + `refresh_auth`) — needs a sample Dhan
   `/optionchain` response to match the normalization exactly.
3. **Calibrate the regime-gate thresholds** for Nifty (the gate drives everything downstream).
4. Build S-A…S-D (pure logic) in parallel with data recording; HTTPS + UI integration last.
