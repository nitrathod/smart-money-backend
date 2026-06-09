from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://smartmoney:smartmoney@db:5432/smartmoney"

    instruments: str = "NIFTY,BANKNIFTY"
    poll_interval_sec: int = 60
    market_open: str = "09:15"
    market_close: str = "15:30"
    token_refresh_ist: str = "08:45"
    log_level: str = "INFO"

    # Broker: "mock" (synthetic, no creds) or "dhan"
    broker: str = "mock"
    dhan_client_id: str = ""
    dhan_access_token: str = ""

    @property
    def instrument_list(self) -> list[str]:
        return [s.strip().upper() for s in self.instruments.split(",") if s.strip()]


settings = Settings()
