"""Motor autônomo de trading — o laço que liga análise a execução.

Este é o único módulo onde o pipeline inteiro se encontra, e por isso é onde um
valor inventado causa mais estrago: o risk manager só protege se os números que
recebe forem reais. Nada aqui é hard-coded — equity vem do broker, perda do dia
vem do banco, e o stop vem da volatilidade medida.

Postura de alavancagem: **dimensionada pelo risco, com teto de tamanho fixo**.
O volume de partida é derivado de `MAX_RISK_PER_TRADE_PCT` do equity e da
distância do stop (história 30) — quanto mais volátil o par, menor o lote para
o mesmo risco monetário. A partir daí (história 32, perfil agressivo), o único
que ainda pode reduzir esse volume é `VOLUME_MAX_PER_ORDER_LOTS` (teto fixo) e
a margem livre real da conta — risco por trade e exposição agregada não
bloqueiam mais ordem. Quando o risco configurado não paga nem o lote mínimo do
broker, ou a margem livre não cobre nem o lote mínimo, a ordem é rejeitada em
vez de sair sub-dimensionada (lote mínimo) ou sobre-arriscada (arredondar para
cima).
"""

from __future__ import annotations

import math
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
from backend.execution.drawdown_guard import (
    is_drawdown_block_active,
    record_equity,
    trigger_drawdown_block,
)
from backend.execution.kill_switch import is_kill_switch_active
from backend.execution.order_manager import OrderManager
from backend.execution.risk_manager import OrderRequest, RiskManager, RiskValidationError
from backend.models.enums import Direction, Side
from backend.models.instrument import Instrument
from backend.models.outcome import Outcome
from backend.models.signal import Signal

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = structlog.get_logger(__name__)

# Candles suficientes para o indicador mais longo (MACD 26 + sinal 9),
# independente do timeframe configurado (`settings.timeframe`): a contagem é
# em número de candles, não em tempo.
CANDLES_POR_CICLO = 120

# Take profit a 2x a distância do stop: relação risco/retorno de 1:2.
#
# Este número não é cosmético. Com RR de 1:2 o breakeven exige ~33% de acerto;
# invertê-lo para 0.2 (arriscar 5 para ganhar 1) exigiria 83,3% — patamar que
# nenhuma estratégia técnica sustenta. Alterar aqui muda a viabilidade
# matemática do sistema inteiro, não só o tamanho do alvo.
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

        if is_drawdown_block_active(session):
            logger.warning("runner.drawdown_bloqueado", acao="ciclo ignorado")
            return

        equity = client.get_account_info().equity
        peak_equity = record_equity(session, equity)
        drawdown_pct = self._drawdown_pct(peak_equity, equity)
        if drawdown_pct >= self.settings.max_drawdown_from_peak_pct:
            reason = (
                f"Drawdown de {drawdown_pct:.2f}% a partir do pico de equity "
                f"{peak_equity} (equity atual {equity})"
            )
            trigger_drawdown_block(session, reason=reason)
            logger.warning(
                "runner.drawdown_bloqueado",
                acao="ciclo ignorado",
                drawdown_pct=round(drawdown_pct, 2),
                pico=peak_equity,
                equity=equity,
            )
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

        if fused.confidence < self.settings.min_signal_confidence:
            # Direção existe, mas a convicção por trás dela não passa no piso
            # calibrado (história 27) — sem este corte, um sinal de 7% de
            # confiança era executado como se fosse de 70%.
            logger.info(
                "runner.confianca_insuficiente",
                symbol=symbol,
                confianca=round(fused.confidence, 3),
                limiar=self.settings.min_signal_confidence,
            )
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

        volume = self._calcular_volume(
            account.equity, distancia_sl, instrument, self.settings.max_risk_per_trade_pct
        )
        if volume is None:
            logger.info(
                "runner.risco_nao_cobre_lote_minimo",
                symbol=symbol,
                min_volume=instrument.min_volume,
            )
            return

        # Teto duro de tamanho por ordem (história 32): nunca acima do que o
        # operador fixou, independente do que o risco calculado pediria.
        volume = min(volume, self.settings.volume_max_per_order_lots)

        # Previne erro "No money" limitando o volume à margem livre real da conta
        folga_margem = self.settings.margin_free_buffer_pct / 100.0
        margin_req_per_lot = (instrument.contract_size * entry) / account.leverage
        max_vol_by_margin = (account.margin_free * folga_margem) / margin_req_per_lot
        passos_margem = math.floor(max_vol_by_margin / instrument.volume_step + 1e-9)
        volume_limite_margem = round(passos_margem * instrument.volume_step, 8)
        volume = min(volume, volume_limite_margem)

        if volume < instrument.min_volume:
            logger.info(
                "runner.margem_insuficiente",
                symbol=symbol,
                margin_free=account.margin_free,
            )
            return

        order_manager.place_order(
            request=OrderRequest(
                symbol=symbol,
                side=side,
                volume=volume,
                price=entry,
                stop_loss=stop_loss,
                take_profit=take_profit,
                min_volume=instrument.min_volume,
            ),
            client_request_id=self._client_request_id(symbol, side),
            instrument_id=instrument.id,
            equity=account.equity,
            daily_loss=self._perda_do_dia(session, client),
            signal_id=signal_id,
        )

    # -- estado real, nunca presumido ---------------------------------------
    @staticmethod
    def _drawdown_pct(peak_equity: float, equity: float) -> float:
        """Percentual de queda do equity atual em relação ao pico já observado.

        Nunca negativo: um novo pico (equity >= peak) não é "drawdown negativo".
        """
        if peak_equity <= 0:
            return 0.0
        return max(0.0, (peak_equity - equity) / peak_equity * 100.0)

    @staticmethod
    def _perda_do_dia(session: Session, client: MT5Client) -> float:
        """Perda do dia, em valor absoluto positivo: realizada + flutuante.

        A parte realizada é lida do banco e não de um contador em memória: o
        processo reinicia, o prejuízo do dia não. A parte flutuante é o P&L
        líquido das posições abertas no broker (soma de todas, lucro e perda
        juntos) — sem ela, posições perdendo ficam invisíveis para o limite
        diário até fechar, exatamente quando já não protegem mais nada
        (história 29). Só o líquido *negativo* piora o resultado do dia: um
        líquido positivo não abate perda já realizada, mas também não soma —
        lucro flutuante ainda pode reverter antes de fechar.
        """
        inicio = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        realizado = session.execute(
            select(func.coalesce(func.sum(Outcome.pnl), 0.0)).where(
                Outcome.created_at >= inicio, Outcome.pnl < 0
            )
        ).scalar_one()

        flutuante = sum(p.profit for p in client.get_positions())
        perda_flutuante = max(0.0, -flutuante)

        return abs(float(realizado)) + perda_flutuante

    @staticmethod
    def _tem_posicao_aberta(client: MT5Client, symbol: str) -> bool:
        """Já existe posição aberta neste símbolo?

        Sem esta trava o bot reabre a mesma direção a cada ciclo enquanto o
        sinal persistir — e um sinal técnico costuma persistir por vários
        minutos. Em uma hora seriam 60 posições empilhadas no mesmo par.
        """
        return any(p.symbol == symbol for p in client.get_positions())

    @staticmethod
    def _calcular_volume(
        equity: float, distancia_sl: float, instrument: Instrument, risk_pct: float
    ) -> float | None:
        """Volume dimensionado pelo risco configurado (história 30).

        `volume = (equity * risco%) / (distância_sl * contract_size)`,
        arredondado **para baixo** no `volume_step` do broker — nunca para
        cima, porque isso infla o risco real acima do configurado. Devolve
        `None` quando o risco não paga nem o lote mínimo do broker: a ordem é
        rejeitada em vez de sair sub-dimensionada (lote mínimo fixo) ou
        sobre-arriscada (arredondar para cima até o mínimo).
        """
        risco_monetario_alvo = equity * (risk_pct / 100.0)
        volume_bruto = risco_monetario_alvo / (distancia_sl * instrument.contract_size)

        # Epsilon absorve erro de ponto flutuante na divisão (ex.: 6.999999999997
        # não pode arredondar para 6 quando o valor exato é 7).
        passos = math.floor(volume_bruto / instrument.volume_step + 1e-9)
        volume = round(passos * instrument.volume_step, 8)

        if volume < instrument.min_volume:
            return None

        return min(volume, instrument.volume_max)

    @staticmethod
    def _get_or_create_instrument(session: Session, symbol: str, client: MT5Client) -> Instrument:
        """Busca ou cria o instrumento, sincronizando os limites de volume com o broker.

        Lote mínimo, passo, lote máximo e tamanho do contrato são lidos do
        broker a cada chamada, não só na criação: é o broker quem decide esses
        valores, e eles podem mudar sem que o registro local seja recriado.
        """
        info = client.get_symbol_info(symbol)

        instrument = session.execute(
            select(Instrument).where(Instrument.symbol == symbol)
        ).scalar_one_or_none()
        if instrument is not None:
            mudou = (
                instrument.min_volume != info.volume_min
                or instrument.volume_step != info.volume_step
                or instrument.volume_max != info.volume_max
                or instrument.contract_size != info.contract_size
            )
            if mudou:
                instrument.min_volume = info.volume_min
                instrument.volume_step = info.volume_step
                instrument.volume_max = info.volume_max
                instrument.contract_size = info.contract_size
                session.commit()
            return instrument

        instrument = Instrument(
            symbol=symbol,
            min_volume=info.volume_min,
            volume_step=info.volume_step,
            volume_max=info.volume_max,
            contract_size=info.contract_size,
        )
        session.add(instrument)
        session.commit()
        return instrument

    @staticmethod
    def _client_request_id(symbol: str, side: Side) -> str:
        """Chave de idempotência com granularidade de 5.5 minutos.

        Dois ciclos dentro do mesmo bloco de 330 segundos, no mesmo símbolo e lado,
        produzem o mesmo id — a segunda tentativa colide no UNIQUE de `trades`.
        """
        now = datetime.now(UTC)
        bucket = int(now.timestamp() // 330)
        return f"bot-{symbol}-{side.value}-{bucket}"
