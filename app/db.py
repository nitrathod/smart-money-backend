import json
from pathlib import Path

import asyncpg

_SCHEMA = (Path(__file__).resolve().parent.parent / "db" / "schema.sql").read_text()


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
            await con.execute(_SCHEMA)

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
