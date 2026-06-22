import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import settings
from .market_calendar import is_market_open
from .providers import get_provider
from .signals.engine import compute_stance

log = logging.getLogger("smartmoney.scheduler")
IST = ZoneInfo("Asia/Kolkata")


class PollState:
    """Lightweight shared state surfaced by /healthz."""

    def __init__(self) -> None:
        self.last_poll: datetime | None = None
        self.market_open: bool = False
        self.regimes: dict[str, str] = {}   # underlying -> last regime (regime hysteresis)

    def mark(self) -> None:
        self.last_poll = datetime.now(IST)

    def seconds_since_last_poll(self) -> float | None:
        if self.last_poll is None:
            return None
        return (datetime.now(IST) - self.last_poll).total_seconds()

    def last_poll_iso(self) -> str | None:
        return self.last_poll.isoformat() if self.last_poll else None


async def poll_once(db, state: PollState) -> None:
    state.market_open = is_market_open()
    if not state.market_open:
        return
    provider = get_provider()
    for underlying in settings.instrument_list:
        try:
            snap = await provider.fetch_chain(underlying)
            await db.save_snapshot(snap)
            state.mark()
            log.info(
                "saved snapshot underlying=%s strikes=%d spot=%s",
                underlying, len(snap["strikes"]), snap["spot"],
            )
        except Exception:
            log.exception("poll failed for %s", underlying)
            continue

        # signal engine: compute + persist the Stance from this snapshot + recorded history
        try:
            now = datetime.now(IST)
            day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            history = await db.get_recent_snapshots(underlying, limit=30, before_ts=snap["ts_ist"])
            day_open = await db.get_day_open_snapshot(underlying, day_start)
            out = compute_stance(snap, day_open, history, state.regimes.get(underlying), now_ist=now)
            await db.save_stance(out)
            state.regimes[underlying] = out["stance"]["regime"]
            log.info(
                "stance underlying=%s regime=%s dir=%s conv=%s action=%s size=%s",
                underlying, out["stance"]["regime"], out["stance"]["direction"],
                out["stance"]["conviction"], out["stance"]["action"], out["stance"]["size_factor"],
            )
        except Exception:
            log.exception("stance computation failed for %s", underlying)


async def refresh_token() -> None:
    provider = get_provider()
    try:
        await provider.refresh_auth()
        log.info("broker token refresh ok")
    except Exception:
        log.exception("broker token refresh failed")


def build_scheduler(db, state: PollState) -> AsyncIOScheduler:
    sched = AsyncIOScheduler(timezone=IST)
    # Fire at the top of every minute (wall-clock aligned, no drift).
    sched.add_job(
        poll_once, CronTrigger(second=0, timezone=IST),
        args=[db, state], id="poll",
        max_instances=1, coalesce=True, misfire_grace_time=10,
    )
    h, m = settings.token_refresh_ist.split(":")
    sched.add_job(
        refresh_token, CronTrigger(hour=int(h), minute=int(m), timezone=IST),
        id="token_refresh",
    )
    return sched
