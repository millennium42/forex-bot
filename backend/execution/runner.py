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
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy import func, select

from backend.analysis.sentiment_analyzer import (
    SentimentAnalyzer,
    SentimentScore,
    SentimentUnavailableError,
)
from backend.analysis.signal_fusion import DEFAULT_WEIGHTS, FusedSignal, fuse_signals
from backend.analysis.strategy import Strategy, StrategySignal, build_enabled_strategies
from backend.analysis.technical_analyzer import TechnicalScore, compute_indicators
from backend.collection.documents import recent_documents
from backend.collection.mt5_client import MT5Client, MT5ConnectionError, Position
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
from backend.models.audit_log import AuditLog
from backend.models.enums import AuditEventType, Direction, Side, TradeStatus
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
CANDLES_POR_CICLO = 600


class BotRunner:
    """Percorre os símbolos configurados avaliando e, se couber, executando."""

    def __init__(
        self,
        symbols: list[str],
        interval_seconds: int = 60,
        settings: Settings | None = None,
        sentiment_analyzer: SentimentAnalyzer | None = None,
        strategies: list[Strategy] | None = None,
    ) -> None:
        self.symbols = symbols
        self.interval_seconds = interval_seconds
        self.settings = settings or get_settings()
        self._sentiment_analyzer = sentiment_analyzer
        # Registro de estratégias paralelas (história 39): cada uma avalia o
        # mesmo OHLC por conta própria, a cada ciclo, para cada símbolo. Nome
        # desconhecido em `STRATEGIES_ENABLED` falha aqui, no boot — não no
        # meio de um ciclo.
        self._strategies = (
            strategies
            if strategies is not None
            else build_enabled_strategies(self.settings.strategies_enabled_list)
        )

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

    def _registrar_bloqueio(
        self,
        session: Session,
        motivo: str,
        symbol: str,
        strategy: str = "technical",
        **detalhes: Any,
    ) -> None:
        """Grava por que uma ordem não saiu (história 36).

        O dashboard mostra o motivo mais recente. Kill switch e drawdown já
        persistem seu próprio motivo (históricas 18/29, eventos
        `KILL_SWITCH_TRIGGERED`/`DRAWDOWN_LIMIT_TRIGGERED`); os demais
        bloqueios do runner (confiança, cooldown, margem, ATR/stop inválidos,
        risco que não cobre o lote mínimo) só existiam como log estruturado
        (`structlog`), nunca consultável pela API.

        **Só grava quando o motivo muda** para aquele símbolo+estratégia. Um
        bloqueio persistente — cooldown de vários minutos com ciclo de 33s —
        repetia a mesma linha a cada ciclo: 290 registros por hora dizendo a
        mesma coisa, num log append-only que nunca é podado. O que interessa
        ao operador é a transição ("passou a bloquear por cooldown"), não a
        repetição. A partir da história 39, a chave de dedupe inclui a
        estratégia: duas estratégias bloqueando pelo mesmo motivo no mesmo
        símbolo, no mesmo ciclo, são eventos distintos, não repetição.
        """
        ultimo = session.scalar(
            select(AuditLog)
            .where(AuditLog.event_type == AuditEventType.ORDER_BLOCKED)
            .order_by(AuditLog.id.desc())
            .limit(1)
        )
        if (
            ultimo is not None
            and ultimo.payload.get("symbol") == symbol
            and ultimo.payload.get("motivo") == motivo
            and ultimo.payload.get("estrategia") == strategy
        ):
            return

        session.add(
            AuditLog(
                event_type=AuditEventType.ORDER_BLOCKED,
                payload={"motivo": motivo, "symbol": symbol, "estrategia": strategy, **detalhes},
            )
        )
        session.commit()

    # -- um símbolo ----------------------------------------------------------
    def _process_symbol(
        self,
        symbol: str,
        client: MT5Client,
        session: Session,
        order_manager: OrderManager,
    ) -> None:
        """Avalia TODAS as estratégias habilitadas para o símbolo (história 39).

        ATR e sentimento são lidos uma vez e compartilhados entre as
        estratégias: são dado de mercado (volatilidade) e contexto (notícia),
        não opinião — cada estratégia decide direção/confiança por conta
        própria, mas o stop e a fusão com sentimento usam a mesma base.
        """
        candles = client.get_candles(symbol, self.settings.mt5_timeframe, CANDLES_POR_CICLO)
        indicadores = compute_indicators(candles)
        if indicadores is None:
            # Série curta ou indicador indefinido. Não é erro — é ausência de
            # informação, e ausência de informação não vira decisão de trade.
            logger.debug("runner.sem_indicadores", symbol=symbol)
            return

        sentiment = self._obter_sentimento(session, symbol)
        instrument = self._get_or_create_instrument(session, symbol, client)

        for strategy in self._strategies:
            try:
                resultado = strategy.evaluate(candles)
            except Exception as exc:
                # Isolamento: uma estratégia quebrada não pode derrubar as
                # outras no mesmo ciclo (AC7 da história 39).
                logger.error(
                    "runner.estrategia_falhou",
                    symbol=symbol,
                    estrategia=strategy.name,
                    erro=str(exc),
                )
                continue

            if resultado is None:
                logger.debug("runner.sem_setup", symbol=symbol, estrategia=strategy.name)
                continue

            self._avaliar_estrategia(
                symbol,
                strategy.name,
                resultado,
                sentiment,
                indicadores.atr,
                client,
                session,
                order_manager,
                instrument,
            )

    def _avaliar_estrategia(
        self,
        symbol: str,
        strategy_name: str,
        resultado: StrategySignal,
        sentiment: SentimentScore | None,
        atr: float,
        client: MT5Client,
        session: Session,
        order_manager: OrderManager,
        instrument: Instrument,
    ) -> None:
        """Funde o sinal de uma estratégia com o sentimento e decide se executa."""
        technical_like = TechnicalScore(
            score=resultado.score, confidence=resultado.confidence, components=resultado.components
        )
        fused = fuse_signals(technical=technical_like, sentiment=sentiment)
        logger.info(
            "runner.avaliado",
            symbol=symbol,
            estrategia=strategy_name,
            direcao=fused.direction.value,
            confianca=round(fused.confidence, 3),
        )

        signal = self._registrar_signal(
            session, instrument, fused, resultado, strategy_name, sentiment
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
                estrategia=strategy_name,
                confianca=round(fused.confidence, 3),
                limiar=self.settings.min_signal_confidence,
            )
            self._registrar_bloqueio(
                session,
                "confianca_insuficiente",
                symbol,
                strategy_name,
                confianca=round(fused.confidence, 3),
                limiar=self.settings.min_signal_confidence,
            )
            return

        self._executar(
            symbol,
            fused,
            atr,
            client,
            session,
            order_manager,
            instrument,
            signal.id,
            strategy_name,
            resultado.stop_loss,
            resultado.take_profit_rr,
            resultado.risk_pct,
            resultado.volume_max_lots,
        )

    def _registrar_signal(
        self,
        session: Session,
        instrument: Instrument,
        fused: FusedSignal,
        resultado: StrategySignal,
        strategy: str,
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
            strategy=strategy,
            direction=fused.direction,
            confidence=fused.confidence,
            fused_score=fused.score,
            sentiment_score=sentiment.score if sentiment is not None else None,
            sentiment_confidence=sentiment.confidence if sentiment is not None else None,
            technical_score=resultado.score,
            weight_version=fused.weight_version,
            inputs={
                "components": resultado.components,
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

        Desligado (`SENTIMENT_ENABLED=false`), nem consulta documento nem
        carrega o analisador: a decisão fica puramente técnica e o `Signal`
        grava sentimento nulo, não zero forjado.
        """
        if not self.settings.sentiment_enabled:
            return None

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
        strategy: str = "technical",
        stop_loss_override: float | None = None,
        take_profit_rr_override: float | None = None,
        risk_pct_override: float | None = None,
        volume_max_lots_override: float | None = None,
    ) -> None:
        if stop_loss_override is None and atr <= 0:
            # ATR zero significa volatilidade não medida. Sem ela não há stop
            # defensável, e ordem sem stop defensável não sai. Só se aplica
            # quando o stop viria do ATR — uma estratégia com stop próprio
            # (história 40) não depende dele.
            logger.warning("runner.atr_invalido", symbol=symbol, atr=atr)
            self._registrar_bloqueio(session, "atr_invalido", symbol, strategy, atr=atr)
            return

        side = Side.BUY if fused.direction is Direction.BUY else Side.SELL

        # Múltiplas posições no símbolo são permitidas (história 34): o que
        # não pode é reabrir a MESMA leitura de mercado a cada ciclo. A partir
        # da história 39, "mesma leitura" é por (símbolo, direção, estratégia)
        # — duas estratégias no mesmo símbolo/direção não colidem entre si.
        if not self._pode_abrir_posicao(session, client, symbol, side, strategy):
            logger.debug(
                "runner.leitura_repetida", symbol=symbol, estrategia=strategy, direcao=side.value
            )
            self._registrar_bloqueio(session, "cooldown", symbol, strategy, direcao=side.value)
            return

        account = client.get_account_info()
        tick = client.get_tick(symbol)
        entry = tick.ask if side is Side.BUY else tick.bid

        if stop_loss_override is not None:
            # Estratégia de padrão gráfico com stop próprio (história 40):
            # o stop vem da leitura dela (ex.: banda de Bollinger), não do
            # ATR global. O alvo continua no mesmo RR configurado, aplicado
            # sobre a distância real desse stop.
            stop_loss = stop_loss_override
            distancia_sl = abs(entry - stop_loss)
        else:
            # Stop por volatilidade (§4 do PRD), não por distância fixa: o
            # mesmo número de pontos significa coisas diferentes em pares
            # diferentes.
            distancia_sl = atr * self.settings.atr_sl_multiplier
            stop_loss = entry - distancia_sl if side is Side.BUY else entry + distancia_sl

        if take_profit_rr_override is not None:
            # A estratégia declara o próprio RR (`TPCoef` da fonte MQL5): cada
            # uma foi desenhada e testada com um alvo específico. Uma seguidora
            # de tendência (3MACD, TPCoef 2.0) erra a maioria das entradas e
            # compensa nas que pegam o movimento — forçá-la ao RR global de
            # 0,333 exigiria 75% de acerto contra os ~60% que ela entrega por
            # natureza, tornando-a estruturalmente perdedora.
            rr = take_profit_rr_override
        else:
            # História 45: alvo monetário dinâmico baseado em % do equity.
            # O stop distance continua do ATR; o TP é posicionado para valer
            # exatamente o ganho em % do equity, independente da distância do SL.
            take_profit_usd = account.equity * (self.settings.take_profit_pct / 100.0)
            loss_usd = account.equity * (self.settings.max_loss_pct_per_trade / 100.0)
            rr = take_profit_usd / loss_usd if loss_usd > 0 else self.settings.take_profit_rr

        distancia_tp = distancia_sl * rr
        take_profit = entry + distancia_tp if side is Side.BUY else entry - distancia_tp

        if stop_loss <= 0 or distancia_sl <= 0:
            logger.warning("runner.stop_invalido", symbol=symbol, stop_loss=stop_loss)
            self._registrar_bloqueio(
                session, "stop_invalido", symbol, strategy, stop_loss=stop_loss
            )
            return

        # Risco por trade: a estratégia tem precedência sobre a config global.
        # O `Risk` da fonte MQL5 é calibrado por estratégia — 0,5% na de
        # tendência, 2,25% na de pullback — porque a frequência e a qualidade
        # dos sinais diferem. `None` cai no `max_loss_pct_per_trade` da config
        # (história 45), que é o caminho de `technical`.
        risco_pct = (
            risk_pct_override
            if risk_pct_override is not None
            else self.settings.max_loss_pct_per_trade
        )
        volume = self._calcular_volume(account.equity, distancia_sl, instrument, risco_pct)
        if volume is None:
            logger.info(
                "runner.risco_nao_cobre_lote_minimo",
                symbol=symbol,
                min_volume=instrument.min_volume,
            )
            self._registrar_bloqueio(
                session, "risco_insuficiente", symbol, strategy, min_volume=instrument.min_volume
            )
            return

        # Teto de tamanho por ordem. Com o stop derivado do ATR, o volume que o
        # risco pede quase sempre estoura o teto — então é ELE, e não o
        # `risk_pct`, que decide o tamanho real da posição.
        #
        # Estratégia portada declara o próprio teto, proporcional ao `Risk` da
        # fonte. As demais caem na curva por confiança (história 46): confiança
        # 10% -> 2.0 lotes, 70% -> 5.0, logarítmico entre elas. A curva só faz
        # sentido para sinal de score contínuo, onde a confiança de fato varia;
        # em estratégia binária ela é sempre 1.0 (fundida 0.7), e usá-la daria
        # o teto máximo justamente a quem não mediu convicção nenhuma.
        if volume_max_lots_override is not None:
            volume_max_dinamico = volume_max_lots_override
        else:
            volume_max_dinamico = self._volume_max_por_confianca(fused.confidence * 100.0)
        volume = min(volume, volume_max_dinamico)

        # Previne erro "No money" limitando o volume à margem livre real da conta
        folga_margem = self.settings.margin_free_buffer_pct / 100.0
        margin_req_per_lot = (instrument.contract_size * entry) / account.leverage
        max_vol_by_margin = (account.margin_free * folga_margem) / margin_req_per_lot
        passos_margem = math.floor(max_vol_by_margin / instrument.volume_step + 1e-9)
        # MT5 aceita apenas 2 casas decimais (0.01 lotes)
        volume_limite_margem = round(passos_margem * instrument.volume_step, 2)
        volume = min(volume, volume_limite_margem)

        if volume < instrument.min_volume:
            logger.info(
                "runner.margem_insuficiente",
                symbol=symbol,
                margin_free=account.margin_free,
            )
            self._registrar_bloqueio(
                session, "margem_insuficiente", symbol, strategy, margin_free=account.margin_free
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
                max_volume=volume_max_dinamico,
            ),
            client_request_id=self._client_request_id(symbol, side, strategy),
            instrument_id=instrument.id,
            equity=account.equity,
            daily_loss=self._perda_do_dia(session, client),
            signal_id=signal_id,
            strategy=strategy,
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

    def _pode_abrir_posicao(
        self,
        session: Session,
        client: MT5Client,
        symbol: str,
        side: Side,
        strategy: str = "technical",
    ) -> bool:
        """Nova posição no símbolo exige leitura materialmente diferente das já abertas.

        Direção oposta a qualquer posição já aberta no símbolo é, por definição,
        uma leitura diferente — permitida sempre, sem cooldown. A MESMA direção
        só é permitida de novo depois de `signal_repeat_cooldown_minutes` desde
        o último `Trade` que de fato chegou ao broker (nunca `REJECTED`) nesse
        símbolo+direção+estratégia (história 39: o cooldown passou a ser por
        estratégia, não mais global do símbolo — duas estratégias podem operar
        a mesma direção no mesmo símbolo sem bloquear uma à outra): o sinal
        técnico persiste por vários ciclos, e sem este intervalo o bot
        empilharia uma posição por ciclo na mesma direção. Sem `Trade` nenhum
        registrado ainda para essa direção+estratégia, não há o que proteger
        — permite.
        """
        direcoes_abertas = {
            self._position_side(p) for p in client.get_positions() if p.symbol == symbol
        }
        if side not in direcoes_abertas:
            return True

        ultimo = session.execute(
            select(Trade.created_at)
            .join(Instrument, Trade.instrument_id == Instrument.id)
            .where(
                Instrument.symbol == symbol,
                Trade.side == side,
                Trade.status != TradeStatus.REJECTED,
                Trade.strategy == strategy,
            )
            .order_by(Trade.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if ultimo is None:
            return True

        # Postgres devolve o timestamp com tzinfo; o SQLite dos testes de
        # unidade não. Sem normalizar, a subtração quebra com TypeError só no
        # teste (ou só em produção, dependendo de onde o valor nasceu) — mesmo
        # gotcha e mesma correção de `outcome_recorder._utc`.
        if ultimo.tzinfo is None:
            ultimo = ultimo.replace(tzinfo=UTC)

        cooldown = timedelta(minutes=self.settings.signal_repeat_cooldown_minutes)
        return datetime.now(UTC) - ultimo >= cooldown

    @staticmethod
    def _position_side(position: Position) -> Side:
        """Direção de uma posição do broker.

        MT5 usa `POSITION_TYPE_BUY=0` / `POSITION_TYPE_SELL=1` — os mesmos
        valores de `ORDER_TYPE_BUY`/`ORDER_TYPE_SELL` já usados no restante do
        módulo, então o mapeamento é direto.
        """
        return Side.BUY if position.type == 0 else Side.SELL

    @staticmethod
    def _volume_max_por_confianca(confidence: float) -> float:
        """Teto de volume dinâmico baseado em confiança do sinal (história 46).

        Confiança 10% → teto 2.0 lotes
        Confiança 70% → teto 5.0 lotes
        Crescimento logarítmico entre eles.

        Acima de 70% continua crescendo logaritmicamente:
        Confiança 100% → ~7.0 lotes (extrapolado pela mesma curva).
        """
        if confidence <= 0:
            return 2.0  # Sinal inválido, teto mínimo

        # Curva logarítmica mapeando [10, 70] → [2.0, 5.0]
        # Para confidence fora do range, extrapolamos mantendo a curva
        log_10 = math.log(10)
        log_70 = math.log(70)
        log_conf = math.log(confidence)

        # Interpolação linear no espaço log
        t = (log_conf - log_10) / (log_70 - log_10)
        volume_max = 2.0 + t * 3.0  # [2.0, 5.0] span = 3.0

        # Clamp mínimo em 2.0 (nunca abaixo, mesmo com conf < 10%)
        # MT5 aceita apenas 2 casas decimais (0.01 lotes)
        return round(max(2.0, volume_max), 2)

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
        # MT5 aceita apenas 2 casas decimais (0.01 lotes), não 8.
        # Arredondar para 2 casas garante compatibilidade com o broker.
        volume = round(passos * instrument.volume_step, 2)

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
    def _client_request_id(symbol: str, side: Side, strategy: str = "technical") -> str:
        """Chave de idempotência com granularidade de 5.5 minutos.

        Dois ciclos dentro do mesmo bloco de 330 segundos, no mesmo símbolo,
        lado e estratégia, produzem o mesmo id — a segunda tentativa colide no
        UNIQUE de `trades`. A estratégia entra na chave (história 39) para que
        duas estratégias decidindo o mesmo símbolo/direção no mesmo bloco não
        colidam entre si — cada uma tem sua própria idempotência.
        """
        now = datetime.now(UTC)
        bucket = int(now.timestamp() // 330)
        return f"bot-{symbol}-{side.value}-{strategy}-{bucket}"
