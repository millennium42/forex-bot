# PROMPT — Entrega Ponta a Ponta: Forex Trading Bot (MT5 + FTMO)

> Cole este bloco inteiro numa sessão nova do Claude Code, dentro da pasta do projeto vazia.
> Ele roda o Ralph Loop do início ao fim: PRD → prd.json → loop autônomo → ship.

---

## PROMPT (copiar a partir daqui)

Você vai construir, do zero até produção, um **Forex Trading Bot autônomo** seguindo o Ralph Loop.
Leia `~/.claude/CLAUDE.md` e `~/.claude/RALPH_WORKFLOW.md` antes de começar. Siga Ponytail (YAGNI) em tudo.

### 1. Contexto e objetivo

Bot que:
1. Coleta notícias e tweets em tempo real sobre pares de moedas.
2. Analisa sentimento (NLP) + análise técnica (RSI, MACD, Bollinger, ATR).
3. Combina os dois sinais em uma decisão com score de confiança.
4. Executa ordens via **MetaTrader 5** (Python `MetaTrader5`), começando em conta **demo/FTMO Challenge**.
5. Aprende: compara previsão vs. resultado real e ajusta pesos dos sinais.
6. Só destrava conta real quando os *gates* de performance passarem (ver §5).

**Regra inegociável:** o bot **nunca** opera conta real por decisão própria. A promoção demo→real é uma
ação manual, explícita, registrada em audit log. `TRADING_MODE=demo` é o default e falha fechado.

### 2. Stack

- **Backend:** Python 3.12, FastAPI, Celery + Redis, SQLAlchemy
- **Broker:** MetaTrader5 (terminal local, socket 127.0.0.1), corretora FTMO / IC Markets / Blueberry
- **Banco:** PostgreSQL (append-only para trades e decisões)
- **NLP:** transformers (FinBERT ou similar) + fallback TextBlob
- **TA:** pandas + `ta` (não reimplementar indicador que já existe — Ponytail)
- **Frontend:** Next.js + TypeScript strict + shadcn/ui
- **Infra:** Docker Compose, GitHub Actions, Sentry
- **Testes:** pytest + coverage ≥ 80%, vitest no front

### 3. Arquitetura em camadas

```
layer 1 — collection/    news_collector, twitter_collector, market_data (MT5 ticks)
layer 2 — analysis/      sentiment_analyzer, technical_analyzer, signal_fusion
layer 3 — execution/     risk_manager (gate), order_manager (MT5), position_tracker
layer 4 — learning/      outcome_recorder, weight_optimizer, backtester
layer 5 — api/           FastAPI (REST + WS) → dashboard Next.js
```

Fluxo: `collection → analysis → signal (score) → risk_manager → order_manager → outcome → learning`.
`risk_manager` é o único caminho para ordem. Nenhum módulo chama `order_send` direto.

### 4. Regras de risco (hard-coded, testadas)

- Máx. **1%** do equity por trade, **3%** de exposição aberta total.
- Máx. **5%** de perda no dia → kill switch, para de operar até reset manual.
- Stop loss obrigatório em toda ordem (ATR-based). Ordem sem SL é rejeitada.
- Máx. N trades/dia (config). Sem operar 15 min antes/depois de evento macro de alto impacto.
- Compatível com regras FTMO (daily loss / max drawdown) — validar antes de cada ordem.

### 5. Gates de promoção (demo → real)

Só libera modo real quando **todos** forem verdade sobre ≥ 200 trades demo:
- win rate ≥ 55%
- Sharpe ≥ 1.0
- max drawdown ≤ 10%
- profit factor ≥ 1.3
- desvio backtest vs. forward test < 15%

Implementar como `promotion_gate.py` com teste unitário para cada critério.

### 6. Auditabilidade e compliance

- Tabela `audit_log` append-only, `client_request_id` obrigatório em toda ordem (idempotência).
- Toda decisão gravada com: sinais de entrada, pesos aplicados, score, resultado.
- Sem credenciais em log. Sem PII. `.env` fora do git, `.env.example` versionado.
- Sem mocks em produção: se MT5 não conectar, o sistema **para**, não simula.

### 7. Estrutura do repositório

```
forex-bot/
├── CLAUDE.md              # customizado p/ este stack (comandos de verificação)
├── AGENTS.md              # Ralph atualiza a cada iteração
├── prd.json               # histórias + passes
├── progress.txt           # aprendizados (append-only)
├── scripts/ralph/ralph.sh
├── tasks/prd-forex-bot.md
├── backend/
│   ├── collection/ analysis/ execution/ learning/ api/
│   ├── models/ migrations/ tests/
├── frontend/              # Next.js dashboard
├── docker-compose.yml
├── .github/workflows/ci.yml
└── .env.example
```

### 8. Histórias (Ralph-sized — cada uma cabe em 1 contexto)

1. Scaffold do repo: estrutura, Docker Compose (postgres+redis), `.env.example`, CI verde vazio.
2. Modelos + migrations: `instruments`, `signals`, `trades`, `outcomes`, `audit_log` (append-only).
3. Conector MT5: `connect()`, `get_ticks()`, `get_account_info()`; falha fechado se offline.
4. News collector: RSS/API → fila Celery → persiste (dedupe por hash de URL).
5. Twitter collector: stream filtrado por cashtags/pares → mesma fila.
6. Sentiment analyzer: texto → score [-1, 1] + confiança; cacheado no Redis.
7. Technical analyzer: OHLC → RSI, MACD, Bollinger, ATR → score normalizado.
8. Signal fusion: combina sentimento + técnica com pesos versionados → decisão + confiança.
9. Risk manager: valida 1%/3%/5%, SL obrigatório, regras FTMO. Único caminho p/ ordem.
10. Order manager: `place`, `modify`, `close` via MT5, com `client_request_id` idempotente.
11. Position tracker: reconcilia posições MT5 vs. banco a cada N segundos.
12. Outcome recorder: fecha o loop — trade encerrado → grava resultado vs. previsão.
13. Weight optimizer: ajusta pesos do signal fusion a partir dos outcomes (versionado, reversível).
14. Backtester: replay de histórico MT5 com o mesmo pipeline de decisão.
15. Promotion gate: os 5 critérios do §5 + endpoint que reporta status atual.
16. API FastAPI: REST + WebSocket de eventos ao vivo.
17. Dashboard Next.js: equity curve, trades abertos, sinais recentes, status dos gates.
18. Kill switch: manual (endpoint) e automático (perda diária), com audit log.
19. Observabilidade: Sentry, health checks, métricas de latência do pipeline.
20. Hardening: rate limits, retry/backoff no MT5, testes de falha de rede, coverage ≥ 80%.

### 9. Definition of Done (por história)

- `pytest` passa, coverage ≥ 80% no módulo tocado
- `ruff` + `mypy` limpos; frontend: `tsc --noEmit` + `eslint`
- CI verde
- Commit granular, mensagem descritiva
- `AGENTS.md` e `progress.txt` atualizados
- P0 = 0, P1 = 0

### 10. Execução

```
/prd criar PRD do Forex Trading Bot conforme este documento
/ralph converter tasks/prd-forex-bot.md para prd.json
./scripts/ralph/ralph.sh 30
```

Comece pela história 1. Não pule etapas. Não invente requisito fora deste documento.

---

## Nota

Isto é engenharia de software, não recomendação de investimento. Os parâmetros de risco são
defaults conservadores para um sistema de estudo — valide você mesmo antes de qualquer capital real.
