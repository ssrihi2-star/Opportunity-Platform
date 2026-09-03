"""Token-bucket rate limiting. Redis when available, in-memory otherwise (tests)."""

from __future__ import annotations

import time
from collections import defaultdict

from app.core.config import settings


class InMemoryLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, list[float]] = defaultdict(list)

    async def allow(self, key: str, limit: int, window_seconds: int = 60) -> bool:
        now = time.monotonic()
        bucket = self._hits[key]
        cutoff = now - window_seconds
        bucket[:] = [t for t in bucket if t > cutoff]
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        return True

    async def retry_after(self, key: str, window_seconds: int = 60) -> float:
        bucket = self._hits.get(key) or []
        if not bucket:
            return 0.0
        return max(0.0, window_seconds - (time.monotonic() - bucket[0]))


class RedisLimiter:
    def __init__(self, url: str) -> None:
        import redis.asyncio as redis  # lazy import so tests need no redis

        self._redis = redis.from_url(url, encoding="utf-8", decode_responses=True)

    async def allow(self, key: str, limit: int, window_seconds: int = 60) -> bool:
        pipe = self._redis.pipeline()
        pipe.incr(key, 1)
        pipe.expire(key, window_seconds)
        count, _ = await pipe.execute()
        return int(count) <= limit

    async def retry_after(self, key: str, window_seconds: int = 60) -> float:
        ttl = await self._redis.ttl(key)
        return float(ttl) if ttl and ttl > 0 else float(window_seconds)


_limiter: InMemoryLimiter | RedisLimiter | None = None


def get_limiter() -> InMemoryLimiter | RedisLimiter:
    global _limiter
    if _limiter is None:
        if settings.ENV == "test" or not settings.REDIS_URL:
            _limiter = InMemoryLimiter()
        else:
            try:
                _limiter = RedisLimiter(settings.REDIS_URL)
            except Exception:  # noqa: BLE001 - degrade rather than fail startup
                _limiter = InMemoryLimiter()
    return _limiter


def reset_limiter() -> None:
    global _limiter
    _limiter = None
