"""Rate limiting simples via Redis."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from redis import Redis

from backend.config import get_settings


def get_redis_client() -> Redis:
    """Retorna cliente Redis configurado."""
    settings = get_settings()
    return Redis.from_url(settings.redis_url, decode_responses=True)


class RateLimiter:
    """Validador de rate limit baseado em token bucket fixo."""

    def __init__(self, requests: int, window: int) -> None:
        self.requests = requests
        self.window = window

    def __call__(
        self,
        request: Request,
        redis: Redis = Depends(get_redis_client),  # noqa: B008
    ) -> None:
        client_ip = request.client.host if request.client else "127.0.0.1"
        key = f"rate_limit:{client_ip}:{request.url.path}"

        current = redis.get(key)
        if current and int(current) >= self.requests:
            raise HTTPException(status_code=429, detail="Too Many Requests")

        pipe = redis.pipeline()
        pipe.incr(key)
        # Se for o primeiro request da janela, define a expiração
        if not current:
            pipe.expire(key, self.window)
        pipe.execute()
