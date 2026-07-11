"""
Qanuni.AI — Caching Layer
==========================
Redis-backed caching with automatic in-memory fallback.
Caches expensive LLM responses, web search queries, and RAG lookups.
"""

import os
import json
import time
import threading
from typing import Optional, Any

REDIS_URL = os.getenv("REDIS_URL", "")
CACHE_ENABLED = os.getenv("CACHE_ENABLED", "1") == "1"

class InMemoryCache:
    """Fallback thread-safe in-memory cache with TTL support."""
    def __init__(self):
        self._store = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            if key not in self._store:
                return None
            val, expiry = self._store[key]
            if expiry is not None and time.time() > expiry:
                del self._store[key]
                return None
            return val

    def set(self, key: str, val: Any, ttl: Optional[int] = None):
        with self._lock:
            expiry = time.time() + ttl if ttl is not None else None
            self._store[key] = (val, expiry)

    def delete(self, key: str):
        with self._lock:
            self._store.pop(key, None)

    def clear(self):
        with self._lock:
            self._store.clear()


class CacheManager:
    """Manages Redis connection and delegates to InMemoryCache on failure."""
    def __init__(self):
        self.enabled = CACHE_ENABLED
        self.redis_client = None
        self.fallback = InMemoryCache()
        
        if self.enabled and REDIS_URL:
            try:
                import redis
                self.redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
                # Test connection
                self.redis_client.ping()
                print("[Cache] Connected to Redis successfully")
            except Exception as e:
                print(f"[Cache] Redis connection failed, using in-memory fallback: {e}")
                self.redis_client = None

    def get(self, key: str) -> Optional[Any]:
        if not self.enabled:
            return None
        
        if self.redis_client:
            try:
                val = self.redis_client.get(key)
                if val is not None:
                    return json.loads(val)
            except Exception as e:
                print(f"[Cache] Redis get error: {e}")
                # Fall back to in-memory
                return self.fallback.get(key)
        else:
            return self.fallback.get(key)

    def set(self, key: str, val: Any, ttl: Optional[int] = 3600):
        if not self.enabled:
            return
        
        if self.redis_client:
            try:
                serialized = json.dumps(val, ensure_ascii=False)
                if ttl:
                    self.redis_client.setex(key, ttl, serialized)
                else:
                    self.redis_client.set(key, serialized)
            except Exception as e:
                print(f"[Cache] Redis set error: {e}")
                self.fallback.set(key, val, ttl)
        else:
            self.fallback.set(key, val, ttl)

    def delete(self, key: str):
        if self.redis_client:
            try:
                self.redis_client.delete(key)
            except Exception:
                self.fallback.delete(key)
        else:
            self.fallback.delete(key)


# Global singleton instance
cache = CacheManager()

def cache_result(ttl: int = 3600, key_prefix: str = ""):
    """Decorator to cache function return values based on arguments."""
    def decorator(func):
        def wrapper(*args, **kwargs):
            if not cache.enabled:
                return func(*args, **kwargs)
            
            # Build cache key from function name + serialized args
            arg_str = f"{args}:{sorted(kwargs.items())}"
            key = f"qanuni:{key_prefix or func.__name__}:{arg_str}"
            
            cached_val = cache.get(key)
            if cached_val is not None:
                return cached_val
            
            res = func(*args, **kwargs)
            cache.set(key, res, ttl)
            return res
        return wrapper
    return decorator
