from functools import lru_cache

from ..config import settings
from .mock import MockProvider


@lru_cache
def get_provider():
    if settings.broker == "dhan":
        from .dhan import DhanProvider
        return DhanProvider(settings.dhan_client_id, settings.dhan_access_token)
    return MockProvider()
