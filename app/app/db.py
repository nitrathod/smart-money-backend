import json
from pathlib import Path

import asyncpg

from .signals.metrics import _to_dt

_DB_DIR = Path(__file__).resolve().parent.parent / "db"
_SCHEMA = (_DB_DIR / "schema.sql").read_text()
_TIMESCALE = (_DB_DIR / "timescale_optional.sql").read_text()


class Database:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self.pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        self.pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=5)

    async def close(self) -> None:
        if self.pool:
            await self.pool.close()

    async def ensure_schema(self) -> None:
        assert self.pool
        async with self.pool.acquire() as con:
            await con.execute(_SCHEMA)            # plain Postgres — always works
            # Best-effort upgrade to TimescaleDB hypertables if the extension exists.
            try:
                await con.execute(_TIMESCALE)
            except Exception:
                pass  # vanilla Postgres (e.g. Koyeb) — plain tables are fine

    async def save_snapshot(self, snap: dict) -> None:
        assert self.pool
        async with self.pool.acquire() as con:
            await con.execute(
                """
                INSERT INTO chain_snapshot (ts, underlying, expiry, spot, payload)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (underlying, ts) DO NOTHING
                """,
                snap["ts_ist"],
                snap["underlying"],
                snap["expiry"],
                snap["spot"],
                json.dumps(snap, default=str),
            )

    async def get_recent_snapshots(self, underlying: str, limit: int = 30, before_ts=None) -> list[dict]:
        """Most-recent snapshots for an underlying, returned OLDEST -> NEWEST."""
        assert self.pool
        async with self.pool.acquire() as con:
            if before_ts is not None:
                rows = await con.fetch(
                    "SELECT payload FROM chain_snapshot WHERE underlying=$1 AND ts < $2 "
                    "ORDER BY ts DESC LIMIT $3",
                    underlying, before_ts, limit,
                )
            else:
                rows = await con.fetch(
                    "SELECT payload FROM chain_snapshot WHERE underlying=$1 "
                    "ORDER BY ts DESC LIMIT $2",
                    underlying, limit,
                )
        snaps = [json.loads(r["payload"]) for r in rows]
        snaps.reverse()
        return snaps

    async def get_day_open_snapshot(self, underlying: str, day_start) -> dict | None:
        """Earliest snapshot at/after the start of the IST trading day (day-open OI baseline)."""
        assert self.pool
        async with self.pool.acquire() as con:
            row = await con.fetchrow(
                "SELECT payload FROM chain_snapshot WHERE underlying=$1 AND ts >= $2 "
                "ORDER BY ts ASC LIMIT 1",
                underlying, day_start,
            )
        return json.loads(row["payload"]) if row else None

    async def save_stance(self, out: dict) -> None:
        """Persist the full engine output; index the headline fields on the stance row."""
        assert self.pool
        st = out["stance"]
        async with self.pool.acquire() as con:
            await con.execute(
                """
                INSERT INTO stance (ts, underlying, regime, direction, action, payload)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (underlying, ts) DO UPDATE SET
                  regime=EXCLUDED.regime, direction=EXCLUDED.direction,
                  action=EXCLUDED.action, payload=EXCLUDED.payload
                """,
                _to_dt(st["ts_ist"]), st["underlying"], st.get("regime"),
                st.get("direction"), st.get("action"), json.dumps(out, default=str),
            )

    async def get_latest_stance(self, underlying: str) -> dict | None:
        assert self.pool
        async with self.pool.acquire() as con:
            row = await con.fetchrow(
                "SELECT payload FROM stance WHERE underlying=$1 ORDER BY ts DESC LIMIT 1",
                underlying,
            )
        return json.loads(row["payload"]) if row else None
