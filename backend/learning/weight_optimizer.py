"""Otimizador de pesos do Signal Fusion.

Ajusta os pesos de sentimento e técnica com base nos resultados reais (outcomes)
dos trades. As mudanças são versionadas para garantir rastreabilidade e permitir rollback,
cumprindo a história 13.
"""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from backend.analysis.signal_fusion import DEFAULT_WEIGHTS, FusionWeights
from backend.models.outcome import Outcome
from backend.models.signal import Signal
from backend.models.weight_version import WeightVersion

__all__ = ["WeightOptimizer"]


class WeightOptimizer:
    """Ajusta pesos do signal fusion a partir de resultados reais, mantendo histórico."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_current_weights(self) -> FusionWeights:
        """Retorna os pesos ativos atualmente no banco ou o DEFAULT_WEIGHTS."""
        stmt = (
            select(WeightVersion)
            .where(WeightVersion.is_active == True)  # noqa: E712
            .order_by(WeightVersion.created_at.desc())
            .limit(1)
        )
        row = self.session.execute(stmt).scalar_one_or_none()

        if row:
            return FusionWeights(
                technical=row.technical,
                sentiment=row.sentiment,
                version=row.version,
            )

        return DEFAULT_WEIGHTS

    def set_weights(self, version: str, technical: float, sentiment: float) -> WeightVersion:
        """Cria e ativa uma nova versão de pesos, desativando a anterior.

        Garante que versões antigas não sejam sobrescritas (insert-only de versões).
        """
        # Verifica se já existe a versão
        existing = self.session.execute(
            select(WeightVersion).where(WeightVersion.version == version)
        ).scalar_one_or_none()

        if existing:
            raise ValueError(f"Versão {version} já existe. Não sobrescrevemos histórico.")

        # Desativa ativos atuais
        self.session.execute(
            update(WeightVersion)
            .where(WeightVersion.is_active == True)  # noqa: E712
            .values(is_active=False)
        )

        new_version = WeightVersion(
            version=version,
            technical=technical,
            sentiment=sentiment,
            is_active=True,
        )
        self.session.add(new_version)
        self.session.flush()
        return new_version

    def rollback_to_version(self, version: str) -> WeightVersion:
        """Retorna a uma versão antiga de pesos.

        Desativa a versão atual e reativa a versão alvo.
        """
        target = self.session.execute(
            select(WeightVersion).where(WeightVersion.version == version)
        ).scalar_one_or_none()

        if not target:
            raise ValueError(f"Versão {version} não encontrada.")

        # Desativa todas
        self.session.execute(
            update(WeightVersion)
            .where(WeightVersion.is_active == True)  # noqa: E712
            .values(is_active=False)
        )
        # Reativa alvo
        target.is_active = True
        self.session.flush()
        return target

    def optimize(self, new_version: str, limit: int = 100) -> FusionWeights | None:
        """Otimiza pesos com base nos últimos 'limit' outcomes.

        Avalia a proporção de acertos de cada componente (técnico vs sentimento).
        Cria e ativa a 'new_version' se houver dados suficientes, caso contrário
        retorna None e não altera nada.
        """
        stmt = (
            select(Outcome, Signal)
            .join(Signal, Outcome.signal_id == Signal.id)
            .order_by(Outcome.created_at.desc())
            .limit(limit)
        )
        rows = self.session.execute(stmt).all()

        if not rows:
            return None

        tech_correct = 0.0
        sent_correct = 0.0
        total_eval = 0

        for outcome, signal in rows:
            if outcome.actual_direction.name == "HOLD":
                continue

            tech_score = signal.technical_score or 0.0
            sent_score = signal.sentiment_score or 0.0
            actual = outcome.actual_direction.name

            # Conta ponto se a direção do componente estava alinhada com o resultado real
            if (tech_score > 0 and actual == "BUY") or (tech_score < 0 and actual == "SELL"):
                tech_correct += 1
            if (sent_score > 0 and actual == "BUY") or (sent_score < 0 and actual == "SELL"):
                sent_correct += 1

            total_eval += 1

        if total_eval == 0:
            return None

        # Normaliza (mínimo 0.1 para nunca isolar totalmente um componente)
        base_tech = max(tech_correct / total_eval, 0.1)
        base_sent = max(sent_correct / total_eval, 0.1)

        total_base = base_tech + base_sent
        new_tech = round(base_tech / total_base, 2)
        new_sent = round(base_sent / total_base, 2)

        # Ajusta pequeno desvio de rounding para que a soma seja ~1.0
        new_sent = round(1.0 - new_tech, 2)

        # Se os novos pesos forem idênticos à versão ativa, não faz nada
        current = self.get_current_weights()
        if abs(current.technical - new_tech) < 0.01 and abs(current.sentiment - new_sent) < 0.01:
            return current

        self.set_weights(new_version, technical=new_tech, sentiment=new_sent)
        return FusionWeights(technical=new_tech, sentiment=new_sent, version=new_version)
