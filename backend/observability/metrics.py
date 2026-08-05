"""Métricas e instrumentação do sistema."""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager

import structlog

logger = structlog.get_logger(__name__)


@contextmanager
def measure_latency(stage: str) -> Iterator[None]:
    """Mede e loga a latência de um estágio do pipeline."""
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed = time.perf_counter() - start
        logger.info("pipeline_latency", stage=stage, elapsed_s=elapsed)
