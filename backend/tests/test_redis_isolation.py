"""
The suite runs on the production box as a deploy gate (scripts/deploy.sh),
so every Redis-backed helper must be pointed at the dedicated test database
(index 15) that conftest.py forces into REDIS_URL before the app is imported.
"""
import redis.asyncio as redis_async

from app.config import settings
from app.services import alerts, otp, rate_limit
from conftest import TEST_REDIS_DB, TEST_REDIS_URL


def test_settings_redis_url_is_the_dedicated_test_database():
    assert settings.redis_url == TEST_REDIS_URL
    assert settings.redis_url.endswith(f"/{TEST_REDIS_DB}")


def test_every_redis_client_is_built_on_the_test_database():
    if rate_limit._redis is not None:
        assert rate_limit._redis.connection_pool.connection_kwargs["db"] == TEST_REDIS_DB
    if alerts._redis_sync is not None:
        assert alerts._redis_sync.connection_pool.connection_kwargs["db"] == TEST_REDIS_DB


async def test_per_loop_client_and_dependants_share_rate_limit_client():
    client = rate_limit._client()
    if client is None:
        return
    assert isinstance(client, redis_async.Redis)
    assert client.connection_pool.connection_kwargs["db"] == TEST_REDIS_DB
    # otp/alerts no longer keep module-level clients of their own — they go through rate_limit._client().
    assert not hasattr(otp, "_redis")
    assert not hasattr(alerts, "_redis")
    assert rate_limit._client() is client  # same loop -> same client
