import logging
from contextlib import asynccontextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import FastAPI

from .config import settings
from .db import Database
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
