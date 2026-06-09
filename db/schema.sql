CREATE EXTENSION IF NOT EXISTS timescaledb;

-- Raw 60s option-chain snapshots (the backtest dataset accumulates here)
CREATE TABLE IF NOT EXISTS chain_snapshot (
  ts          timestamptz NOT NULL,
  underlying  text        NOT NULL,
  expiry      date,
  spot        numeric,
  payload     jsonb       NOT NULL,
  PRIMARY KEY (underlying, ts)
);
SELECT create_hypertable('chain_snapshot', 'ts', if_not_exists => TRUE);

-- Emitted stances (filled once the signal engine lands in Phase 3 / S-D)
CREATE TABLE IF NOT EXISTS stance (
  ts          timestamptz NOT NULL,
  underlying  text        NOT NULL,
  regime      text,
  direction   text,
  action      text,
  payload     jsonb       NOT NULL,
  PRIMARY KEY (underlying, ts)
);
SELECT create_hypertable('stance', 'ts', if_not_exists => TRUE);
