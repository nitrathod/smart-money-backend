import random
from datetime import datetime, date
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

_BASE = {"NIFTY": 23150.0, "BANKNIFTY": 51000.0}
_STEP = {"NIFTY": 50, "BANKNIFTY": 100}


class MockProvider:
    """Synthetic option chain — proves the poll → persist pipeline before a real broker is wired."""

    async def refresh_auth(self) -> None:
        return None

    async def fetch_chain(self, underlying: str) -> dict:
        step = _STEP.get(underlying, 50)
        spot = _BASE.get(underlying, 23000.0) + random.uniform(-40, 40)
        atm = round(spot / step) * step

        strikes = []
        for i in range(-10, 11):
            k = atm + i * step
            ce_ltp = max(spot - k, 0) + random.uniform(1, 60)
            pe_ltp = max(k - spot, 0) + random.uniform(1, 60)
            strikes.append({
                "strike": k,
                "ce": {"oi": random.randint(0, 600_000), "ltp": round(ce_ltp, 2), "iv": round(random.uniform(10, 20), 2)},
                "pe": {"oi": random.randint(0, 600_000), "ltp": round(pe_ltp, 2), "iv": round(random.uniform(10, 20), 2)},
            })

        return {
            "underlying": underlying,
            "expiry": date.today(),
            "ts_ist": datetime.now(IST).replace(microsecond=0),
            "spot": round(spot, 2),
            "strikes": strikes,
        }
