"""Dhan option-chain provider (DhanHQ API v2).

Docs:  https://dhanhq.co/docs/v2/option-chain/
Auth:  every request sends headers `access-token` (JWT) and `client-id`.
Rate:  Option Chain API allows 1 unique request per 3 seconds. We poll once per
       minute per underlying and cache the expiry, so we stay far inside the limit.
       A small internal spacer guarantees >=3s between Dhan calls regardless.

Normalizes Dhan's response into the ChainSnapshot dict described in base.py.
"""

import asyncio
import time as _time
from datetime import date, datetime
from zoneinfo import ZoneInfo

import httpx

IST = ZoneInfo("Asia/Kolkata")

# Underlying -> (Dhan security id, exchange segment).
# NIFTY = 13 is confirmed in Dhan's docs. Verify others against the instrument
# master if a fetch ever returns an error: https://dhanhq.co/docs/v2/instruments/
_UNDERLYING: dict[str, tuple[int, str]] = {
    "NIFTY": (13, "IDX_I"),
    "BANKNIFTY": (25, "IDX_I"),
    # "FINNIFTY": (27, "IDX_I"),
    # "SENSEX": (51, "IDX_I"),
}

_MIN_INTERVAL = 3.1  # seconds between Dhan option-chain calls (limit is 1 / 3s)


class DhanProvider:
    BASE_URL = "https://api.dhan.co/v2"

    def __init__(self, client_id: str, access_token: str) -> None:
        if not client_id or not access_token:
            raise RuntimeError(
                "BROKER=dhan but DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN are not set."
            )
        self.client_id = client_id
        self.access_token = access_token
        self._client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            timeout=15.0,
            headers={
                "access-token": access_token,
                "client-id": client_id,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        self._lock = asyncio.Lock()
        self._last_call = 0.0
        self._expiry_cache: dict[str, tuple[date, str]] = {}

    async def refresh_auth(self) -> None:
        # Dhan personal access tokens are generated on web.dhan.co with a chosen
        # validity. There is no silent programmatic refresh for a personal token:
        # when it expires, regenerate it and update the DHAN_ACCESS_TOKEN variable
        # on Railway (the service redeploys automatically). Nothing to do here.
        return None

    async def _post(self, path: str, body: dict) -> dict:
        # Serialize calls and respect the 1-request-per-3-seconds rate limit.
        async with self._lock:
            wait = _MIN_INTERVAL - (_time.monotonic() - self._last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            resp = await self._client.post(path, json=body)
            self._last_call = _time.monotonic()
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "success":
            raise RuntimeError(f"Dhan {path} -> {data}")
        return data

    async def _nearest_expiry(self, scrip: int, seg: str, underlying: str) -> str:
        today = datetime.now(IST).date()
        cached = self._expiry_cache.get(underlying)
        if cached and cached[0] == today:
            return cached[1]
        data = await self._post(
            "/optionchain/expirylist", {"UnderlyingScrip": scrip, "UnderlyingSeg": seg}
        )
        expiries = sorted(data.get("data") or [])
        if not expiries:
            raise RuntimeError(f"Dhan returned no expiries for {underlying}")
        future = [e for e in expiries if date.fromisoformat(e) >= today]
        expiry = future[0] if future else expiries[-1]
        self._expiry_cache[underlying] = (today, expiry)
        return expiry

    @staticmethod
    def _side(side: dict | None) -> dict:
        if not side:
            return {}
        g = side.get("greeks") or {}
        return {
            "oi": side.get("oi"),
            "ltp": side.get("last_price"),
            "iv": side.get("implied_volatility"),
            "volume": side.get("volume"),
            "delta": g.get("delta"),
            "gamma": g.get("gamma"),
            "theta": g.get("theta"),
            "vega": g.get("vega"),
            "bid": side.get("top_bid_price"),
            "ask": side.get("top_ask_price"),
            "prev_oi": side.get("previous_oi"),
            "avg_price": side.get("average_price"),
            "security_id": side.get("security_id"),
        }

    async def fetch_chain(self, underlying: str) -> dict:
        underlying = underlying.upper()
        if underlying not in _UNDERLYING:
            raise ValueError(
                f"No Dhan security mapping for {underlying!r}; add it to _UNDERLYING."
            )
        scrip, seg = _UNDERLYING[underlying]
        expiry = await self._nearest_expiry(scrip, seg, underlying)

        data = await self._post(
            "/optionchain",
            {"UnderlyingScrip": scrip, "UnderlyingSeg": seg, "Expiry": expiry},
        )
        payload = data.get("data") or {}
        oc = payload.get("oc") or {}
        spot = float(payload.get("last_price") or 0.0)

        strikes = []
        for strike_key, sides in oc.items():
            strikes.append(
                {
                    "strike": int(round(float(strike_key))),
                    "ce": self._side(sides.get("ce")),
                    "pe": self._side(sides.get("pe")),
                }
            )
        strikes.sort(key=lambda s: s["strike"])

        return {
            "underlying": underlying,
            "expiry": date.fromisoformat(expiry),
            "ts_ist": datetime.now(IST).replace(microsecond=0),
            "spot": round(spot, 2),
            "strikes": strikes,
        }
