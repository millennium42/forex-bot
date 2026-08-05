"""Analisador de sentimento: texto → score em [-1,1] + confiança em [0,1].

Dois backends, escolhidos nesta ordem: **FinBERT** (`transformers`, extra `nlp`)
e, se ele não estiver disponível, **TextBlob**. O import dos dois é tardio — o
extra pesa ~2GB e o CI roda sem ele; importar este módulo não pode exigir nenhum
dos dois instalados.

O resultado é cacheado no Redis por hash do texto. O cache é **best-effort**,
como os coletores: Redis fora do ar degrada para "sempre recalcula", nunca
derruba a análise. Isso não fere o invariante "sem mocks em produção" — o que
não pode ser inventado é preço; sentimento ausente vira confiança zero, e um
sinal com confiança zero não move o dimensionamento da ordem.

A chave do cache inclui o nome do backend: trocar de motor não pode reaproveitar
score produzido por outro modelo.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import structlog

from backend.config import Settings, get_settings

__all__ = [
    "CacheClient",
    "FinBertBackend",
    "SentimentAnalyzer",
    "SentimentBackend",
    "SentimentScore",
    "SentimentUnavailableError",
    "TextBlobBackend",
    "build_cache",
    "cache_key",
    "load_backend",
    "neutral",
    "text_fingerprint",
]

logger = structlog.get_logger(__name__)

CACHE_PREFIX = "sentiment:v1"

FINBERT_ENGINE = "finbert"
TEXTBLOB_ENGINE = "textblob"

# Rótulos do FinBERT e o sinal que cada um imprime no score.
LABEL_SIGN = {"positive": 1.0, "negative": -1.0, "neutral": 0.0}


class SentimentUnavailableError(RuntimeError):
    """Nenhum backend de NLP instalado: nem FinBERT, nem TextBlob."""


def _clamp(valor: float, minimo: float, maximo: float) -> float:
    return max(minimo, min(maximo, valor))


@dataclass(frozen=True, slots=True)
class SentimentScore:
    """Score assinado e confiança, já dentro das faixas contratadas.

    A normalização acontece na construção, então **não existe** instância fora de
    [-1,1] e [0,1] — nem vinda do modelo, nem desserializada do cache.
    """

    score: float
    confidence: float
    engine: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "score", _clamp(float(self.score), -1.0, 1.0))
        object.__setattr__(self, "confidence", _clamp(float(self.confidence), 0.0, 1.0))

    def to_json(self) -> str:
        return json.dumps(
            {"score": self.score, "confidence": self.confidence, "engine": self.engine}
        )

    @classmethod
    def from_json(cls, raw: object) -> SentimentScore | None:
        """Reconstrói do cache. Payload inservível devolve `None` — quem chama recalcula."""
        if isinstance(raw, bytes | bytearray):
            raw = bytes(raw).decode("utf-8", errors="replace")
        if not isinstance(raw, str):
            return None
        try:
            dados = json.loads(raw)
            return cls(
                score=float(dados["score"]),
                confidence=float(dados["confidence"]),
                engine=str(dados["engine"]),
            )
        except (AttributeError, KeyError, TypeError, ValueError):
            return None


def neutral(engine: str) -> SentimentScore:
    """Ausência de informação: score neutro com confiança zero.

    Confiança zero é o que impede um texto vazio ou um rótulo desconhecido de
    entrar no fusion como se fosse leitura de mercado.
    """
    return SentimentScore(score=0.0, confidence=0.0, engine=engine)


# --------------------------------------------------------------------------- #
# Backends
# --------------------------------------------------------------------------- #
@runtime_checkable
class SentimentBackend(Protocol):
    """Motor de NLP capaz de pontuar um texto."""

    @property
    def name(self) -> str: ...

    def score(self, text: str) -> SentimentScore: ...


def _load_finbert_pipeline(model: str) -> Any:
    """Carrega o pipeline do FinBERT. Import tardio: `transformers` é do extra `nlp`."""
    from transformers import pipeline

    # truncation=True: FinBERT corta em 512 tokens; sem isso, notícia longa levanta.
    return pipeline("sentiment-analysis", model=model, truncation=True)


def _load_textblob() -> Any:
    """Carrega a classe TextBlob. Import tardio pelo mesmo motivo do FinBERT."""
    from textblob import TextBlob

    return TextBlob


def _first_prediction(saida: Any) -> dict[str, Any]:
    """Achata a saída do pipeline, que vem como lista (às vezes aninhada)."""
    while isinstance(saida, list) and saida:
        saida = saida[0]
    return saida if isinstance(saida, dict) else {}


class FinBertBackend:
    """FinBERT: modelo treinado em texto financeiro, o caminho preferencial."""

    name = FINBERT_ENGINE

    def __init__(self, model: str, pipeline: Any | None = None) -> None:
        self._pipeline = _load_finbert_pipeline(model) if pipeline is None else pipeline

    def score(self, text: str) -> SentimentScore:
        """Converte o rótulo do modelo em score assinado.

        A probabilidade da classe vencedora é a confiança — inclusive para
        `neutral`, onde ela mede quão certo o modelo está de que não há direção.
        """
        pred = _first_prediction(self._pipeline(text))
        label = str(pred.get("label", "")).strip().lower()
        sinal = LABEL_SIGN.get(label)
        if sinal is None:
            # Rótulo fora do vocabulário conhecido: não dá para inferir direção.
            logger.warning("sentiment.rotulo_desconhecido", label=label, engine=self.name)
            return neutral(self.name)
        prob = float(pred.get("score", 0.0))
        return SentimentScore(score=sinal * prob, confidence=prob, engine=self.name)


class TextBlobBackend:
    """Fallback léxico.

    Não entende contexto financeiro; existe para o sistema continuar pontuando
    quando o extra `nlp` não está instalado.
    """

    name = TEXTBLOB_ENGINE

    def __init__(self, factory: Any | None = None) -> None:
        self._factory = _load_textblob() if factory is None else factory

    def score(self, text: str) -> SentimentScore:
        """`polarity` já vive em [-1,1].

        A confiança é |polarity|: o TextBlob não devolve probabilidade, e a
        intensidade da polaridade é o único sinal de convicção disponível.
        """
        polarity = float(self._factory(text).sentiment.polarity)
        return SentimentScore(score=polarity, confidence=abs(polarity), engine=self.name)


def load_backend(settings: Settings | None = None) -> SentimentBackend:
    """FinBERT se der; TextBlob se não. Sem nenhum dos dois, levanta.

    Falhar aqui é intencional: um analisador que devolvesse zero para tudo
    passaria despercebido e viraria um sinal permanentemente neutro no fusion.
    """
    settings = settings or get_settings()
    try:
        return FinBertBackend(settings.sentiment_model)
    except (ImportError, OSError, RuntimeError) as exc:
        # OSError/RuntimeError cobrem falha de download e peso local ausente.
        logger.warning("sentiment.finbert_indisponivel", erro=str(exc))

    try:
        backend = TextBlobBackend()
    except ImportError as exc:
        raise SentimentUnavailableError(
            "nenhum backend de sentimento disponível: instale o extra `nlp`"
        ) from exc
    logger.info("sentiment.fallback_textblob")
    return backend


# --------------------------------------------------------------------------- #
# Cache
# --------------------------------------------------------------------------- #
@runtime_checkable
class CacheClient(Protocol):
    """Superfície do Redis usada aqui. Injetável para teste."""

    def get(self, name: str) -> Any: ...

    def set(self, name: str, value: str, ex: int | None = None) -> Any: ...


def build_cache(settings: Settings | None = None) -> CacheClient:
    """Client Redis a partir da config. Não conecta agora: `from_url` é preguiçoso."""
    from redis import Redis

    settings = settings or get_settings()
    client: CacheClient = Redis.from_url(settings.redis_url)
    return client


def text_fingerprint(text: str) -> str:
    """sha256 do texto com espaços normalizados.

    A normalização faz o mesmo conteúdo, recebido com quebras de linha diferentes
    por fonte, cair na mesma entrada de cache.
    """
    return hashlib.sha256(" ".join(text.split()).encode("utf-8")).hexdigest()


def cache_key(engine: str, text: str) -> str:
    return f"{CACHE_PREFIX}:{engine}:{text_fingerprint(text)}"


# --------------------------------------------------------------------------- #
# Analisador
# --------------------------------------------------------------------------- #
class SentimentAnalyzer:
    """Cache na frente, modelo atrás."""

    def __init__(
        self,
        backend: SentimentBackend | None = None,
        cache: CacheClient | None = None,
        ttl_seconds: int | None = None,
        settings: Settings | None = None,
    ) -> None:
        settings = settings or get_settings()
        self._backend = backend or load_backend(settings)
        self._cache = cache
        self._ttl = settings.sentiment_cache_ttl_seconds if ttl_seconds is None else ttl_seconds

    @property
    def engine(self) -> str:
        return self._backend.name

    def analyze(self, text: str) -> SentimentScore:
        """Score do texto, servido do cache quando já calculado."""
        if not text.strip():
            # Sem texto não há o que pontuar — e o modelo não é chamado à toa.
            return neutral(self.engine)

        chave = cache_key(self.engine, text)
        em_cache = self._cache_get(chave)
        if em_cache is not None:
            return em_cache

        resultado = self._backend.score(text)
        self._cache_set(chave, resultado)
        return resultado

    # -- cache: toda falha é degradação, nunca exceção que sobe ------------- #
    def _cache_get(self, chave: str) -> SentimentScore | None:
        if self._cache is None:
            return None
        try:
            bruto = self._cache.get(chave)
        except Exception as exc:
            # Redis fora do ar não pode parar a análise.
            logger.warning("sentiment.cache_indisponivel", operacao="get", erro=str(exc))
            return None
        if bruto is None:
            return None
        resultado = SentimentScore.from_json(bruto)
        if resultado is None:
            logger.warning("sentiment.cache_corrompido", chave=chave)
        return resultado

    def _cache_set(self, chave: str, resultado: SentimentScore) -> None:
        if self._cache is None:
            return
        try:
            self._cache.set(chave, resultado.to_json(), ex=self._ttl)
        except Exception as exc:
            # Idem: gravar no cache é otimização, não requisito.
            logger.warning("sentiment.cache_indisponivel", operacao="set", erro=str(exc))
