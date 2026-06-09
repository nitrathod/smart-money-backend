# Smart Money backend — Phase 0 deployment

A minimal, always-on backend skeleton: a wall-clock-aligned 60s poller, IST market-hours
gating, TimescaleDB persistence, a `/healthz` endpoint, and a daily broker-token refresh.
Ships with a **mock provider** so you can deploy and verify the pipeline before wiring a broker.

## What's here
```
docker-compose.yml      TimescaleDB + the app (restart: unless-stopped = always-on)
Dockerfile              Python 3.12 image (arm64-ready for Oracle Ampere)
requirements.txt        pinned deps (APScheduler stays on v3)
.env.example            copy to .env and edit
db/schema.sql           chain_snapshot + stance hypertables
app/
  main.py               FastAPI app + lifespan + /healthz
  config.py             typed settings from .env
  market_calendar.py    IST trading-day + hours gate
  scheduler.py          60s poll (top-of-minute) + daily token refresh
  db.py                 asyncpg pool + snapshot persistence
  providers/
    base.py             ChainProvider interface (the ChainSnapshot contract)
    mock.py             synthetic chain (BROKER=mock)
    dhan.py             real-broker skeleton (two methods to fill)
```

## Local smoke test (any machine with Docker)
```bash
cp .env.example .env          # keep BROKER=mock for now
docker compose up --build -d
curl localhost:8000/healthz   # -> {"status":"ok", "broker":"mock", ...}
docker compose logs -f app    # watch "saved snapshot ..." each minute (during market hours)
```
> Outside 09:15–15:30 IST the poller correctly idles; `market_open` will be `false`.

## Deploy on the Oracle Always Free VM
1. **Provision:** Oracle Cloud > Compute > Instance. Shape **Ampere A1 (VM.Standard.A1.Flex)**,
   ~2 OCPU / 12 GB is plenty. Image **Ubuntu 24.04 (aarch64)**. Region **Mumbai/Hyderabad**
   (low latency to your broker). If you hit "out of capacity", retry across availability
   domains or convert the account to pay-as-you-go (still free within limits).
2. **Open the port:** add an ingress rule for TCP **8000** in the VCN security list, and on the box:
   `sudo ufw allow 8000/tcp` (or restrict 8000 to your monitoring source).
3. **Install Docker:**
   ```bash
   curl -fsSL https://get.docker.com | sudo sh
   sudo usermod -aG docker $USER && newgrp docker
   sudo systemctl enable docker      # survives reboot
   ```
4. **Time sync** (accurate top-of-minute polling): `sudo apt install -y chrony && sudo systemctl enable --now chrony`
5. **Deploy:**
   ```bash
   git clone <your-repo> smart-money-backend && cd smart-money-backend
   cp .env.example .env && nano .env     # set a strong POSTGRES_PASSWORD + matching DATABASE_URL
   docker compose up --build -d
   curl localhost:8000/healthz
   ```
   `restart: unless-stopped` + Docker enabled on boot = the backend comes back after any reboot.
6. **Monitor:** point UptimeRobot / BetterStack at `http://<vm-public-ip>:8000/healthz`
   (alert if `status != ok` during market hours). This is liveness alerting — the VM doesn't sleep.

## Going live with the real broker
1. Implement `refresh_auth()` and `fetch_chain()` in `app/providers/dhan.py`, normalizing to the
   `ChainSnapshot` dict in `app/providers/base.py`.
2. In `.env`: set `BROKER=dhan`, `DHAN_CLIENT_ID`, `DHAN_ACCESS_TOKEN`.
3. `docker compose up -d --build`. Snapshots now persist real chains — your backtest dataset starts filling.

## Notes
- Keep `.env` out of git (already in `.gitignore`).
- Maintain `NSE_HOLIDAYS` in `market_calendar.py` (or fetch from the broker holiday API).
- Redis is intentionally absent — at this scale one process + Postgres is enough. Add it only
  when you split workers.
- Pin `timescale/timescaledb:latest-pg16` to a specific version before you call this production.
