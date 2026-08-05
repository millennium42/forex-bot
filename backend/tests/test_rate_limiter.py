"""Testes para o Rate Limiter e falhas de rede simuladas (História 20)."""

from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from backend.api.rate_limiter import RateLimiter, get_redis_client


class DummyRedis:
    def __init__(self) -> None:
        self.cache: dict[str, int] = {}

    def get(self, key: str) -> str | None:
        return str(self.cache.get(key)) if key in self.cache else None

    def pipeline(self) -> Any:
        class DummyPipe:
            def __init__(self, parent: DummyRedis) -> None:
                self.parent = parent

            def incr(self, k: str) -> None:
                self.parent.cache[k] = self.parent.cache.get(k, 0) + 1

            def expire(self, k: str, v: int) -> None:
                pass

            def execute(self) -> None:
                pass

        return DummyPipe(self)


def test_rate_limiter_bloqueia_excesso() -> None:
    """O rate limiter deve permitir requests até o limite e bloquear depois (429)."""
    app = FastAPI()
    limiter = RateLimiter(requests=2, window=60)

    # Injeta a dependência localmente com override do redis
    dummy_redis = DummyRedis()

    def override_get_redis() -> DummyRedis:
        return dummy_redis

    app.dependency_overrides[get_redis_client] = override_get_redis

    @app.get("/test", dependencies=[Depends(limiter)])
    def route() -> str:
        return "ok"

    client = TestClient(app)

    # 1ª requisição (ok)
    res1 = client.get("/test")
    assert res1.status_code == 200

    # 2ª requisição (ok)
    res2 = client.get("/test")
    assert res2.status_code == 200

    # 3ª requisição (limite excedido)
    res3 = client.get("/test")
    assert res3.status_code == 429
    assert res3.json()["detail"] == "Too Many Requests"
