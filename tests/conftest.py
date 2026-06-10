import pytest
from httpx import ASGITransport, AsyncClient

from web4ai.api.app import app
from web4ai.cache.store import InMemoryCacheStore
from web4ai.extractor.cached import CachedExtractor
from web4ai.extractor.hybrid import HybridExtractor


@pytest.fixture
def cache_store():
    return InMemoryCacheStore()


@pytest.fixture
def hybrid_extractor():
    return HybridExtractor(renderer=None)


@pytest.fixture
async def client(cache_store):
    app.state.extractor = CachedExtractor(HybridExtractor(), store=cache_store)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
