import logging
from contextlib import asynccontextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

from dataclasses import replace

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .db import Database
from .providers import get_provider
from .signals.engine import compute_stance
from .signals.backtest import replay, evaluate
from .signals.params import params_for
from .scheduler import build_scheduler, PollState

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("smartmoney")
IST = ZoneInfo("Asia/Kolkata")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db = Database(settings.database_url)
    await db.connect()
    await db.ensure_schema()

    state = PollState()
    scheduler = build_scheduler(db, state)
    scheduler.start()

    app.state.db = db
    app.state.scheduler = scheduler
    app.state.poll_state = state
    log.info("startup complete; broker=%s instruments=%s", settings.broker, settings.instrument_list)
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        await db.close()
        log.info("shutdown complete")


app = FastAPI(title="Smart Money backend", lifespan=lifespan)

# Read-only public data for now, so the Vercel UI can fetch it from the browser.
# Tighten allow_origins (and add an access token) when the security pass lands.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/healthz")
async def healthz():
    state: PollState = app.state.poll_state
    age = state.seconds_since_last_poll()
    # During market hours we expect a poll within ~2 intervals.
    healthy = (not state.market_open) or (age is not None and age < settings.poll_interval_sec * 2)
    return {
        "status": "ok" if healthy else "stale",
        "now_ist": datetime.now(IST).isoformat(),
        "market_open": state.market_open,
        "last_poll_ist": state.last_poll_iso(),
        "last_poll_age_sec": round(age, 1) if age is not None else None,
        "broker": settings.broker,
    }


@app.get("/debug/fetch")
async def debug_fetch(underlying: str = "NIFTY"):
    """One-off live fetch to verify broker wiring. Does NOT save. Safe to remove later."""
    provider = get_provider()
    try:
        snap = await provider.fetch_chain(underlying.upper())
    except Exception as e:  # surface the real reason in the browser
        raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}")
    strikes = snap.get("strikes", [])
    atm = min(strikes, key=lambda s: abs(s["strike"] - snap["spot"]), default=None)
    return {
        "underlying": snap["underlying"],
        "expiry": str(snap["expiry"]),
        "ts_ist": snap["ts_ist"].isoformat(),
        "spot": snap["spot"],
        "strike_count": len(strikes),
        "atm_sample": atm,
        "broker": settings.broker,
    }


@app.get("/stance/latest")
async def stance_latest(underlying: str = "NIFTY"):
    """The Oracle's latest posture for an underlying (the public output)."""
    out = await app.state.db.get_latest_stance(underlying.upper())
    if not out:
        raise HTTPException(status_code=404, detail="no stance recorded yet")
    return out["stance"]


@app.get("/metrics/latest")
async def metrics_latest(underlying: str = "NIFTY"):
    """The derived metrics behind the latest stance (PCR, max pain, gamma pin, IV, ...)."""
    out = await app.state.db.get_latest_stance(underlying.upper())
    if not out:
        raise HTTPException(status_code=404, detail="no metrics recorded yet")
    return out.get("metrics", {})


@app.get("/debug/stance")
async def debug_stance(underlying: str = "NIFTY"):
    """Compute a full stance live, on demand (fetch + history + engine). Does NOT save."""
    underlying = underlying.upper()
    db = app.state.db
    provider = get_provider()
    try:
        snap = await provider.fetch_chain(underlying)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}")
    now = datetime.now(IST)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    history = await db.get_recent_snapshots(underlying, limit=30)
    day_open = await db.get_day_open_snapshot(underlying, day_start)
    return compute_stance(snap, day_open, history, None, now_ist=now)


@app.get("/backtest")
async def backtest(underlying: str = "NIFTY", limit: int = 500, horizon: int = 15,
                   floor: float | None = None, conv: float | None = None):
    """Replay recorded snapshots through the engine and report the distribution + hit rate.

    Optional threshold overrides for calibration sweeps:
      ?floor=0.10  -> theta_floor (stand-aside conviction floor)
      ?conv=0.40   -> theta_conv  (strong-conviction threshold)
    """
    underlying = underlying.upper()
    limit = max(50, min(limit, 1500))  # bound memory/time
    snaps = await app.state.db.get_recent_snapshots(underlying, limit=limit)
    if not snaps:
        raise HTTPException(status_code=404, detail="no snapshots recorded for this underlying")
    p = params_for(underlying)
    overrides = {}
    if floor is not None:
        overrides["theta_floor"] = floor
    if conv is not None:
        overrides["theta_conv"] = conv
    if overrides:
        p = replace(p, **overrides)
    results = replay(snaps, params=p)
    summary = evaluate(results, horizon_min=horizon)
    summary["underlying"] = underlying
    summary["thresholds"] = {"theta_floor": p.theta_floor, "theta_conv": p.theta_conv}
    return summary
