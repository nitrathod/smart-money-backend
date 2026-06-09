-- Base schema: PLAIN PostgreSQL. Works on any managed Postgres (Railway, etc.).

CREATE TABLE IF NOT EXISTS chain_snapshot (
  ts          timestamptz NOT NULL,
  underlying  text        NOT NULL,
  expiry      date,
  spot        numeric,
  payload     jsonb       NOT NULL,
  PRIMARY KEY (underlying, ts)
);
CREATE INDEX IF NOT EXISTS idx_chain_snapshot_ts ON chain_snapshot (ts);

CREATE TABLE IF NOT EXISTS stance (
  ts          timestamptz NOT NULL,
  underlying  text        NOT NULL,
  regime      text,
  direction   text,
  action      text,
  payload     jsonb       NOT NULL,
  PRIMARY KEY (underlying, ts)
);
CREATE INDEX IF NOT EXISTS idx_stance_ts ON stance (ts);
