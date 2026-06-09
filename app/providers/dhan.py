import httpx


class DhanProvider:
    """Skeleton for the Dhan option-chain API.

    Docs: https://dhanhq.co/docs/v2/option-chain/
    Rate limit: one chain request per underlying every 3 seconds — a 60s cadence is well inside it.

    Two methods to implement:
      1. refresh_auth() — renew the daily access token (Dhan tokens are short-lived).
      2. fetch_chain()  — map underlying -> (UnderlyingScrip, UnderlyingSeg, Expiry),
                          POST /optionchain, then NORMALIZE the response into the
                          ChainSnapshot dict described in providers/base.py.
    """

    BASE_URL = "https://api.dhan.co/v2"

    def __init__(self, client_id: str, access_token: str) -> None:
        self.client_id = client_id
        self.access_token = access_token
        self._client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            timeout=10.0,
            headers={
                "access-token": access_token,
                "client-id": client_id,
                "Content-Type": "application/json",
            },
        )

    async def refresh_auth(self) -> None:
        # TODO: implement daily token renewal for your Dhan login flow.
        return None

    async def fetch_chain(self, underlying: str) -> dict:
        # TODO: build the request body for `underlying`, call POST /optionchain,
        #       and normalize to the ChainSnapshot dict (see providers/base.py).
        raise NotImplementedError(
            "Wire Dhan /optionchain and normalize to the ChainSnapshot dict. "
            "Until then keep BROKER=mock in .env."
        )
