# PRD — Forex Trading Bot (MT5 + FTMO)

**Versão:** 1.0
**Data:** 2026-08-04
**Owner:** Millani
**Branch:** `feat/forex-bot`

> Isto é engenharia de software, não recomendação de investimento. Os parâmetros de risco são
> defaults conservadores para um sistema de estudo.

---

## 1. Objetivo

Bot autônomo que combina **sentimento de notícias/tweets** com **análise técnica** para gerar
decisões de trade em pares de forex, executadas via **MetaTrader 5**, começando obrigatoriamente
em conta **demo / FTMO Challenge**.

O bot fecha o loop de aprendizado: cada decisão é comparada com o resultado real e os pesos dos
sinais são reajustados.

### Regra inegociável

O bot **nunca** opera conta real por decisão própria. A promoção demo→real é ação manual,
explícita e registrada em `audit_log`. `TRADING_MODE=demo` é o default e o sistema **falha fechado**:
qualquer ambiguidade de configuração resolve para `demo`.

---

## 2. Stack

| Camada | Tecnologia |
|---|---|
| Backend | Python 3.12, FastAPI, Celery + Redis, SQLAlchemy 2.x |
| Broker | `MetaTrader5` (terminal local, 127.0.0.1) — FTMO / IC Markets / Blueberry |
| Banco | PostgreSQL 16 (append-only para `trades`, `signals`, `audit_log`) |
| NLP | `transformers` (FinBERT) com fallback `TextBlob` |
| TA | `pandas` + `ta` (não reimplementar indicador existente — Ponytail) |
| Frontend | Next.js 15, TypeScript strict, shadcn/ui |
| Infra | Docker Compose, GitHub Actions, Sentry |
| Testes | pytest + coverage ≥ 80%, vitest no front |

---

## 3. Arquitetura em camadas

```
layer 1 — collection/    news_collector, twitter_collector, market_data (MT5 ticks)
layer 2 — analysis/      sentiment_analyzer, technical_analyzer, signal_fusion
layer 3 — execution/     risk_manager (gate), order_manager (MT5), position_tracker
layer 4 — learning/      outcome_recorder, weight_optimizer, backtester
layer 5 — api/           FastAPI (REST + WS) → dashboard Next.js
```

Fluxo: `collection → analysis → signal (score) → risk_manager → order_manager → outcome → learning`

**Invariante arquitetural:** `risk_manager` é o único caminho para uma ordem. Nenhum módulo chama
`order_send` direto. Violação disso é bug P0.

---

## 4. Regras de risco (hard-coded, testadas)

| Regra | Valor default | Config |
|---|---|---|
| Risco máximo por trade | 1% do equity | `MAX_RISK_PER_TRADE_PCT` |
| Exposição aberta total | 3% do equity | `MAX_TOTAL_EXPOSURE_PCT` |
| Perda diária máxima | 5% → kill switch | `MAX_DAILY_LOSS_PCT` |
| Stop loss | obrigatório, ATR-based | `ATR_SL_MULTIPLIER` |
| Trades por dia | N configurável | `MAX_TRADES_PER_DAY` |
| Blackout macro | 15 min antes/depois de evento de alto impacto | `MACRO_BLACKOUT_MINUTES` |
| Regras FTMO | daily loss / max drawdown validados antes de cada ordem | `FTMO_*` |

Kill switch diário só reseta por ação manual registrada em audit log.

---

## 5. Gates de promoção (demo → real)

Libera modo real apenas quando **todos** forem verdade sobre **≥ 200 trades demo**:

1. Win rate ≥ 55%
2. Sharpe ≥ 1.0
3. Max drawdown ≤ 10%
4. Profit factor ≥ 1.3
5. Desvio backtest vs. forward test < 15%

Implementado em `learning/promotion_gate.py`, com teste unitário por critério.
Passar nos gates **não** promove: apenas habilita a promoção manual.

---

## 6. Auditabilidade e compliance

- `audit_log` append-only (trigger que bloqueia UPDATE/DELETE).
- `client_request_id` obrigatório em toda ordem → idempotência.
- Toda decisão gravada com: sinais de entrada, pesos aplicados, score, resultado.
- Sem credenciais em log. Sem PII. `.env` fora do git; `.env.example` versionado.
- Sem mocks em produção: se MT5 não conectar, o sistema **para**, não simula.

---

## 7. Histórias

| # | Título | Descrição | Critérios de aceite |
|---|---|---|---|
| 1 | Scaffold do repo | Estrutura de pastas, Docker Compose (postgres+redis), `.env.example`, CI verde vazio | `pytest` roda (0 testes falhando), `ruff`/`mypy` limpos, CI verde, `.env` ignorado |
| 2 | Modelos + migrations | `instruments`, `signals`, `trades`, `outcomes`, `audit_log` | Migration aplica e reverte; append-only testado (UPDATE/DELETE falham) |
| 3 | Conector MT5 | `connect()`, `get_ticks()`, `get_account_info()` | Falha fechado se offline; sem fallback simulado; retry com backoff |
| 4 | News collector | RSS/API → fila Celery → persiste | Dedupe por hash de URL; sem duplicatas em reprocesso |
| 5 | Twitter collector | Stream filtrado por cashtags/pares | Mesma fila do news; dedupe por tweet id |
| 6 | Sentiment analyzer | Texto → score [-1,1] + confiança | Cache Redis por hash do texto; fallback TextBlob se modelo indisponível |
| 7 | Technical analyzer | OHLC → RSI, MACD, Bollinger, ATR → score normalizado | Score em [-1,1]; usa lib `ta`; testado com séries fixtures |
| 8 | Signal fusion | Sentimento + técnica com pesos versionados | Decisão + confiança; versão do peso gravada na decisão |
| 9 | Risk manager | Valida 1%/3%/5%, SL obrigatório, FTMO | Único caminho p/ ordem; cada regra tem teste; rejeita ordem sem SL |
| 10 | Order manager | `place`, `modify`, `close` via MT5 | `client_request_id` idempotente: 2ª chamada não duplica ordem |
| 11 | Position tracker | Reconcilia MT5 vs. banco a cada N segundos | Divergência gera alerta em audit log |
| 12 | Outcome recorder | Trade encerrado → resultado vs. previsão | Fecha o loop; outcome ligado ao signal que o originou |
| 13 | Weight optimizer | Ajusta pesos do fusion a partir dos outcomes | Versionado e reversível; nunca sobrescreve versão anterior |
| 14 | Backtester | Replay de histórico MT5 no mesmo pipeline | Mesmo código de decisão do live (sem branch de teste) |
| 15 | Promotion gate | 5 critérios do §5 + endpoint de status | Teste unitário por critério; endpoint retorna passa/falha por critério |
| 16 | API FastAPI | REST + WebSocket de eventos ao vivo | OpenAPI válido; WS emite sinais e trades |
| 17 | Dashboard Next.js | Equity curve, trades abertos, sinais, gates | `tsc --noEmit` + eslint limpos |
| 18 | Kill switch | Manual (endpoint) e automático (perda diária) | Ambos gravam audit log; reset só manual |
| 19 | Observabilidade | Sentry, health checks, métricas de latência | `/health` e `/ready`; latência por estágio do pipeline |
| 20 | Hardening | Rate limits, retry/backoff MT5, falha de rede, coverage ≥ 80% | Coverage global ≥ 80%; testes de falha de rede |

---

## 8. Definition of Done (por história)

- `pytest` passa, coverage ≥ 80% no módulo tocado
- `ruff` + `mypy` limpos; frontend: `tsc --noEmit` + `eslint`
- CI verde
- Commit granular, mensagem descritiva
- `AGENTS.md` e `progress.txt` atualizados
- P0 = 0, P1 = 0

---

## 9. Fora de escopo (v1)

- Múltiplas contas simultâneas
- Execução em brokers não-MT5
- Otimização de hiperparâmetros por RL
- Mobile app
