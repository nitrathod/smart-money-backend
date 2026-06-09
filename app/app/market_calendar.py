from datetime import datetime, time, date
from zoneinfo import ZoneInfo

from .config import settings

IST = ZoneInfo("Asia/Kolkata")

# TODO: keep this current or fetch from the broker's holiday API at startup.
# Weekends are handled automatically; only add exchange holidays / special sessions here.
NSE_HOLIDAYS: set[date] = set()  # e.g. {date(2026, 1, 26), date(2026, 3, 6)}


def _parse(hhmm: str) -> time:
    h, m = hhmm.split(":")
    return time(int(h), int(m))


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in NSE_HOLIDAYS


def is_market_open(now: datetime | None = None) -> bool:
    now = now or datetime.now(IST)
    if not is_trading_day(now.date()):
        return False
    return _parse(settings.market_open) <= now.time() <= _parse(settings.market_close)
