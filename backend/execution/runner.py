"""Motor autônomo de trading — o laço que liga análise a execução.

Este é o único módulo onde o pipeline inteiro se encontra, e por isso é onde um
valor inventado causa mais estrago: o risk manager só protege se os números que
recebe forem reais. Nada aqui é hard-coded — equity vem do broker, exposição
vem das posições abertas, perda do dia vem do banco, e o stop vem da
volatilidade medida.

Postura de alavancagem: **a mais conservadora possível**. O volume é sempre o
lote mínimo, e ainda assim a ordem só passa se o risco monetário resultante
couber no limite de 1% do equity. Não cabendo, não opera.
"""

from __future__ import annotations

import time
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import func, select

from backend.analysis.sentiment_analyzer import (
    SentimentAnalyzer,
    SentimentScore,
    SentimentUnavailableError,
)
from backend.analysis.signal_fusion import DEFAULT_WEIGHTS, FusedSignal, fuse_signals
from backend.analysis.technical_analyzer import (
    IndicatorSnapshot,
    TechnicalAnalyzer,
    TechnicalScore,
    compute_indicators,
)
from backend.collection.documents import recent_documents
from backend.collection.mt5_client import MT5Client, MT5ConnectionError
from backend.config import Settings, get_settings
from backend.db import session_scope
from backend.execution.kill_switch import is_kill_switch_active
from backend.execution.order_manager import OrderManager
from backend.execution.risk_manager import OrderRequest, RiskManager, RiskValidationError
from backend.models.enums import Direction, Side, TradeStatus
from backend.models.instrument import Instrument
from backend.models.outcome import Outcome
from backend.models.signal import Signal
from backend.models.trade import Trade

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = structlog.get_logger(__name__)

# Candles suficientes para o indicador mais longo (MACD 26 + sinal 9),
# independente do timeframe configurado (`settings.timeframe`): a contagem é
# em número de candles, não em tempo.
CANDLES_POR_CICLO = 120

# Take profit a 2x a distância do stop: relação risco/retorno de 1:2.
RR_RATIO = 2.0


class BotRunner:
    """Percorre os símbolos configurados avaliando e, se couber, executando."""

    def __init__(
        self,
        symbols: list[str],
        interval_seconds: int = 60,
        settings: Settings | None = None,
        sentiment_analyzer: SentimentAnalyzer | None = None,
    ) -> None:
        self.symbols = symbols
        self.interval_seconds = interval_seconds
        self.settings = settings or get_settings()
        self.analyzer = TechnicalAnalyzer()
        self._sentiment_analyzer = sentiment_analyzer

    # -- laço principal ------------------------------------------------------
    def run(self, max_cycles: int | None = None) -> None:
        """Roda o laço. `max_cycles=None` é infinito; um inteiro limita o número de ciclos."""
        with MT5Client(settings=self.settings) as client, session_scope() as session:
            account = client.get_account_info()
            logger.info(
                "runner.iniciado",
                symbols=self.symbols,
                demo=account.is_demo,
                equity=account.equity,
            )

            ciclo = 0
            while max_cycles is None or ciclo < max_cycles:
                self.run_cycle(client, session)
                ciclo += 1
                if max_cycles is None or ciclo < max_cycles:
                    time.sleep(self.interval_seconds)

    def run_cycle(self, client: MT5Client, session: Session) -> None:
        """Um ciclo completo sobre todos os símbolos."""
        if is_kill_switch_active(session):
            logger.warning("runner.kill_switch_ativo", acao="ciclo ignorado")
            return

        risk_manager = RiskManager(self.settings)
        order_manager = OrderManager(client, session, risk_manager)

        for symbol in self.symbols:
            try:
                self._process_symbol(symbol, client, session, order_manager)
            except MT5ConnectionError:
                # Falha de terminal encerra o ciclo inteiro. Seguir para o próximo
                # símbolo seria decidir sobre uma visão de mercado já sabidamente
                # quebrada.
                logger.error("runner.mt5_indisponivel", symbol=symbol)
                raise
            except RiskValidationError as exc:
                # Ordem barrada é o sistema funcionando, não falha.
                logger.info("runner.ordem_barrada", symbol=symbol, motivo=str(exc))
            except Exception as exc:
                logger.error("runner.erro_no_simbolo", symbol=symbol, erro=str(exc))
                session.rollback()

    # -- um símbolo ----------------------------------------------------------
    def _process_symbol(
        self,
        symbol: str,
        client: MT5Client,
        session: Session,
        order_manager: OrderManager,
    ) -> None:
        candles = client.get_candles(symbol, self.settings.mt5_timeframe, CANDLES_POR_CICLO)
        indicadores = compute_indicators(candles)
        if indicadores is None:
            # Série curta ou indicador indefinido. Não é erro — é ausência de
            # informação, e ausência de informação não vira decisão de trade.
            logger.debug("runner.sem_indicadores", symbol=symbol)
            return

        technical = self.analyzer.analyze(candles)
        sentiment = self._obter_sentimento(session, symbol)
        fused = fuse_signals(technical=technical, sentiment=sentiment)
        logger.info(
            "runner.avaliado",
            symbol=symbol,
            direcao=fused.direction.value,
            confianca=round(fused.confidence, 3),
        )

        instrument = self._get_or_create_instrument(session, symbol, client)
        signal = self._registrar_signal(
            session, instrument, fused, technical, indicadores, sentiment
        )

        if fused.direction is Direction.HOLD:
            return

        self._executar(
            symbol, fused, indicadores.atr, client, session, order_manager, instrument, signal.id
        )

    def _registrar_signal(
        self,
        session: Session,
        instrument: Instrument,
        fused: FusedSignal,
        technical: TechnicalScore,
        indicadores: IndicatorSnapshot,
        sentiment: SentimentScore | None,
    ) -> Signal:
        """Grava a decisão antes de qualquer execução — inclusive quando é HOLD.

        Sem este registro, `trade.signal_id` fica nulo e o outcome (história 12)
        não tem previsão para comparar com o resultado. HOLD também é gravado:
        a decisão de não operar é dado de aprendizado para calibrar o filtro de
        confiança (história 27), não só as decisões que viraram ordem.
        """
        signal = Signal(
            instrument_id=instrument.id,
            direction=fused.direction,
            confidence=fused.confidence,
            fused_score=fused.score,
            sentiment_score=sentiment.score if sentiment is not None else None,
            sentiment_confidence=sentiment.confidence if sentiment is not None else None,
            technical_score=technical.score,
            weight_version=fused.weight_version,
            inputs={
                "indicators": asdict(indicadores),
                "technical_components": technical.components,
                "weights": {
                    "technical": DEFAULT_WEIGHTS.technical,
                    "sentiment": DEFAULT_WEIGHTS.sentiment,
                    "version": fused.weight_version,
                },
            },
        )
        session.add(signal)
        session.commit()
        return signal

    def _obter_sentimento(self, session: Session, symbol: str) -> SentimentScore | None:
        """Sentimento do par a partir de documentos recentes, ou `None` sem eles.

        A janela de recência (`sentiment_lookback_minutes`) garante que um
        documento velho não influencie a decisão de agora. Ausência de
        documento ou backend de NLP indisponível resultam em `None`, nunca em
        score neutro forjado — o `fuse_signals` já sabe degradar a confiança
        quando o sentimento é `None`.
        """
        desde = datetime.now(UTC) - timedelta(minutes=self.settings.sentiment_lookback_minutes)
        documentos = recent_documents(session, symbol, desde)
        if not documentos:
            return None
        try:
            return self._get_sentiment_analyzer().analyze_documents(documentos)
        except SentimentUnavailableError as exc:
            # Mesma postura best-effort dos coletores: falta de backend de NLP
            # não pode derrubar o ciclo, só empobrecer a decisão para "sem
            # sentimento".
            logger.warning("runner.sentimento_indisponivel", symbol=symbol, erro=str(exc))
            return None

    def _get_sentiment_analyzer(self) -> SentimentAnalyzer:
        """Carrega o analisador sob demanda — só quando há documento para pontuar.

        Mesmo padrão das outras dependências pesadas (MT5Terminal, backend de
        NLP): a injeção via construtor existe para teste, o runtime carrega o
        modelo de verdade na primeira vez que é preciso.
        """
        if self._sentiment_analyzer is None:
            self._sentiment_analyzer = SentimentAnalyzer(settings=self.settings)
        return self._sentiment_analyzer

    def _executar(
        self,
        symbol: str,
        fused: FusedSignal,
        atr: float,
        client: MT5Client,
        session: Session,
        order_manager: OrderManager,
        instrument: Instrument,
        signal_id: int | None = None,
    ) -> None:
        if atr <= 0:
            # ATR zero significa volatilidade não medida. Sem ela não há stop
            # defensável, e ordem sem stop defensável não sai.
            logger.warning("runner.atr_invalido", symbol=symbol, atr=atr)
            return

        # Uma posição por símbolo. O sinal técnico persiste por vários ciclos;
        # sem esta trava o bot empilharia uma posição por minuto no mesmo par.
        if self._tem_posicao_aberta(client, symbol):
            logger.debug("runner.posicao_ja_aberta", symbol=symbol)
            return

        # Teto diário de ordens (§4 do PRD).
        enviados = self._trades_de_hoje(session)
        if enviados >= self.settings.max_trades_per_day:
            logger.info(
                "runner.teto_diario_atingido",
                enviados=enviados,
                limite=self.settings.max_trades_per_day,
            )
            return

        account = client.get_account_info()
        tick = client.get_tick(symbol)

        side = Side.BUY if fused.direction is Direction.BUY else Side.SELL
        entry = tick.ask if side is Side.BUY else tick.bid

        # Stop por volatilidade (§4 do PRD), não por distância fixa: o mesmo
        # número de pontos significa coisas diferentes em pares diferentes.
        distancia_sl = atr * self.settings.atr_sl_multiplier
        stop_loss = entry - distancia_sl if side is Side.BUY else entry + distancia_sl
        take_profit = (
            entry + distancia_sl * RR_RATIO if side is Side.BUY else entry - distancia_sl * RR_RATIO
        )

        if stop_loss <= 0:
            logger.warning("runner.stop_invalido", symbol=symbol, stop_loss=stop_loss)
            return

        risco_monetario = distancia_sl * instrument.min_volume * instrument.contract_size

        order_manager.place_order(
            request=OrderRequest(
                symbol=symbol,
                side=side,
                volume=instrument.min_volume,
                price=entry,
                stop_loss=stop_loss,
                take_profit=take_profit,
                min_volume=instrument.min_volume,
            ),
            client_request_id=self._client_request_id(symbol, side),
            instrument_id=instrument.id,
            equity=account.equity,
            daily_loss=self._perda_do_dia(session),
            current_exposure=self._exposicao_aberta(client, account.equity),
            trade_monetary_risk=risco_monetario,
            signal_id=signal_id,
        )

    # -- estado real, nunca presumido ---------------------------------------
    @staticmethod
    def _perda_do_dia(session: Session) -> float:
        """Perda realizada hoje, em valor absoluto positivo.

        Lida do banco e não de um contador em memória: o processo reinicia, o
        prejuízo do dia não.
        """
        inicio = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        total = session.execute(
            select(func.coalesce(func.sum(Outcome.pnl), 0.0)).where(
                Outcome.created_at >= inicio, Outcome.pnl < 0
            )
        ).scalar_one()
        return abs(float(total))

    def _exposicao_aberta(self, client: MT5Client, equity: float) -> float:
        """Exposição das posições abertas, em percentual do equity.

        Vem do broker, não do banco: posição aberta por fora do bot ainda
        consome risco da conta e precisa contar no limite de 3%.

        O `contract_size` é indispensável aqui. Sem ele, 0.01 lote de EURUSD
        conta como US$ 0,0115 em vez de US$ 1.154 — a exposição sai subestimada
        em cinco ordens de grandeza e o limite de 3% nunca dispara.
        """
        if equity <= 0:
            # Equity não positivo é situação degenerada; devolver 100% faz o
            # risk manager barrar tudo, que é o comportamento seguro.
            return 100.0

        exposto = 0.0
        for p in client.get_positions():
            try:
                contract_size = client.get_symbol_info(p.symbol).contract_size
            except MT5ConnectionError:
                # Símbolo sem metadados: assume o contrato padrão em vez de
                # ignorar a posição. Subestimar exposição é o erro perigoso.
                contract_size = 100_000.0
            exposto += abs(p.volume * contract_size * p.price_open)

        return (exposto / equity) * 100.0

    @staticmethod
    def _tem_posicao_aberta(client: MT5Client, symbol: str) -> bool:
        """Já existe posição aberta neste símbolo?

        Sem esta trava o bot reabre a mesma direção a cada ciclo enquanto o
        sinal persistir — e um sinal técnico costuma persistir por vários
        minutos. Em uma hora seriam 60 posições empilhadas no mesmo par.
        """
        return any(p.symbol == symbol for p in client.get_positions())

    @staticmethod
    def _trades_de_hoje(session: Session) -> int:
        """Ordens enviadas hoje, para o teto diário (§4 do PRD).

        Conta do banco: o teto é por dia, não por execução do processo.
        """
        inicio = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        total = session.execute(
            select(func.count())
            .select_from(Trade)
            .where(Trade.created_at >= inicio, Trade.status != TradeStatus.REJECTED)
        ).scalar_one()
        return int(total)

    @staticmethod
    def _get_or_create_instrument(session: Session, symbol: str, client: MT5Client) -> Instrument:
        """Busca ou cria o instrumento, sincronizando `min_volume` com o broker.

        O lote mínimo é lido do broker a cada chamada, não só na criação: é o
        broker quem decide esse valor, e ele pode mudar sem que o registro
        local seja recriado.
        """
        min_volume = client.get_symbol_info(symbol).volume_min

        instrument = session.execute(
            select(Instrument).where(Instrument.symbol == symbol)
        ).scalar_one_or_none()
        if instrument is not None:
            if instrument.min_volume != min_volume:
                instrument.min_volume = min_volume
                session.commit()
            return instrument

        instrument = Instrument(symbol=symbol, min_volume=min_volume)
        session.add(instrument)
        session.commit()
        return instrument

    @staticmethod
    def _client_request_id(symbol: str, side: Side) -> str:
        """Chave de idempotência com granularidade de minuto.

        Dois ciclos dentro do mesmo minuto, no mesmo símbolo e lado, produzem o
        mesmo id — a segunda tentativa colide no UNIQUE de `trades` em vez de
        abrir uma segunda posição idêntica.
        """
        janela = datetime.now(UTC).replace(second=0, microsecond=0)
        return f"bot-{symbol}-{side.value}-{janela:%Y%m%d%H%M}"
