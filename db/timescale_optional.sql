-- Applied best-effort by db.py. On a TimescaleDB host this upgrades the tables to
-- hypertables; on plain Postgres it errors and db.py skips it harmlessly.
CREATE EXTENSION IF NOT EXISTS timescaledb;
SELECT create_hypertable('chain_snapshot', 'ts', if_not_exists => TRUE, migrate_data => TRUE);
SELECT create_hypertable('stance', 'ts', if_not_exists => TRUE, migrate_data => TRUE);
