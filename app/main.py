import logging
from contextlib import asynccontextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException

from .config import settings
from .db import Database
from .providers import get_provider
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
