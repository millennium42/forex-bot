# forex-bot

Bot de trading em forex que combina **sentimento de notícias/tweets** com **análise técnica**,
executa via **MetaTrader 5** e aprende comparando previsão com resultado real.

> ⚠️ Isto é um projeto de engenharia de software, **não** recomendação de investimento.
> Os parâmetros de risco são defaults conservadores para um sistema de estudo. Valide você mesmo
> antes de qualquer capital real.

---

## Segurança operacional

O bot **nunca** opera conta real por decisão própria. O modo real exige, ao mesmo tempo:

```
TRADING_MODE=real
REAL_TRADING_UNLOCKED=true
```

Qualquer outra combinação — incluindo typos, valores desconhecidos e variável ausente — resolve
para **demo**. A promoção demo→real é ação manual, registrada em `audit_log`, e só faz sentido
depois que os cinco gates de performance passarem (ver [PRD §5](tasks/prd-forex-bot.md)).

---

## Setup

```bash
uv python install 3.12
uv sync --extra dev
cp .env.example .env      # preencha as credenciais; .env nunca vai para o git
docker compose up -d      # postgres + redis
```

O extra `nlp` (transformers + torch, ~2GB) é opcional e só necessário para rodar o FinBERT local:

```bash
uv sync --extra dev --extra nlp
```

## Verificação

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy backend
uv run pytest --cov=backend --cov-report=term-missing
```

---

## Arquitetura

```
collection → analysis → signal → risk_manager → order_manager → outcome → learning
```

| Camada | Pacote | Responsabilidade |
|---|---|---|
| 1 | `backend/collection` | Notícias (RSS), tweets, ticks MT5 |
| 2 | `backend/analysis` | Sentimento, indicadores técnicos, fusão de sinais |
| 3 | `backend/execution` | Risk manager (gate único), order manager, position tracker |
| 4 | `backend/learning` | Outcomes, otimização de pesos, backtester, promotion gate |
| 5 | `backend/api` | FastAPI REST + WebSocket |

`risk_manager` é o **único** caminho para uma ordem. Nenhum outro módulo chama `order_send`.

---

## Documentos

| Arquivo | O quê |
|---|---|
| [tasks/prd-forex-bot.md](tasks/prd-forex-bot.md) | PRD completo — regras de risco, gates, histórias |
| [prd.json](prd.json) | Histórias em formato Ralph, com status |
| [CLAUDE.md](CLAUDE.md) | Invariantes, comandos e convenções para agentes |
| [AGENTS.md](AGENTS.md) | Contexto acumulado entre iterações do Ralph |
| [progress.txt](progress.txt) | Aprendizados, append-only |

## Ralph loop

```bash
./scripts/ralph/ralph.sh 30      # Git Bash no Windows; requer jq e a CLI claude
```
