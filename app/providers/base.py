from typing import Protocol


class ChainProvider(Protocol):
    """Every data source normalizes to the ChainSnapshot dict (see the Oracle spec §1.1).

    fetch_chain returns:
      {
        "underlying": str, "expiry": date, "ts_ist": datetime, "spot": float,
        "strikes": [ {"strike": int,
                      "ce": {"oi": int, "ltp": float, "iv": float, ...},
                      "pe": {"oi": int, "ltp": float, "iv": float, ...}}, ... ]
      }
    """

    async def fetch_chain(self, underlying: str) -> dict: ...

    async def refresh_auth(self) -> None: ...
