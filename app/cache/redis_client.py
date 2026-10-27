# app/cache/redis_client.py
"""
Redis cache wrapper for query result caching and prompt caching.
Falls back gracefully if Redis is unavailable (dev mode without Redis).
"""

import hashlib
import json
import logging
from typing import Any, Optional

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_redis = None


def get_redis():
    global _redis
    if _redis is None:
        try:
            import redis
            _redis = redis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=2,
            )
            _redis.ping()
            logger.info("[Cache] Redis connected at %s", settings.redis_url)
        except Exception as exc:
            logger.warning("[Cache] Redis not available: %s. Caching disabled.", exc)
            _redis = None
    return _redis


def cache_key(prefix: str, content: str) -> str:
    """Generate a deterministic cache key from content hash."""
    h = hashlib.sha256(content.encode()).hexdigest()[:16]
    return f"analytics:{prefix}:{h}"


def get_cached(key: str) -> Optional[Any]:
    """Retrieve a cached value. Returns None if not found or Redis unavailable."""
    r = get_redis()
    if not r:
        return None
    try:
        value = r.get(key)
        if value:
            return json.loads(value)
    except Exception as exc:
        logger.debug("[Cache] Get failed for key %s: %s", key, exc)
    return None


def set_cached(key: str, value: Any, ttl: int | None = None) -> bool:
    """Store a value in Redis cache with optional TTL (seconds)."""
    r = get_redis()
    if not r:
        return False
    try:
        serialized = json.dumps(value, default=str)
        effective_ttl = ttl or settings.cache_ttl_seconds
        r.setex(key, effective_ttl, serialized)
        return True
    except Exception as exc:
        logger.debug("[Cache] Set failed for key %s: %s", key, exc)
        return False
