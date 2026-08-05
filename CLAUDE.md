# CLAUDE.md — forex-bot

Bot de forex: sentimento (NLP) + análise técnica → decisão → execução via MetaTrader 5.
PRD completo em [tasks/prd-forex-bot.md](tasks/prd-forex-bot.md). Histórias em `prd.json`.

---

## Invariantes — violação é bug P0

1. **`TRADING_MODE=demo` é o default e falha fechado.** O modo real exige `TRADING_MODE=real`
   **e** `REAL_TRADING_UNLOCKED=true` simultaneamente. Qualquer outro valor resolve para demo.
   Lógica única em `backend/config.py::Settings.effective_trading_mode`. Não duplicar essa checagem.
2. **`risk_manager` é o único caminho para uma ordem.** Nenhum módulo chama `order_send` direto —
   só `execution/order_manager.py`, e só depois de aprovado pelo risk manager.
3. **`audit_log` é append-only.** Garantido por trigger no banco, não por convenção de código.
4. **Sem mocks em produção.** Se o MT5 não conectar, o sistema para. Nunca simula preço.
5. **`client_request_id` obrigatório em toda ordem.** É a chave de idempotência.
6. **Sem credenciais e sem PII em log.**

---

## Comandos de verificação

```bash
uv sync --extra dev            # instala deps
uv run ruff check .            # lint
uv run ruff format .           # formatação
uv run mypy backend            # typecheck (strict)
uv run pytest                  # testes
uv run pytest --cov=backend --cov-report=term-missing   # coverage (fail_under=80)
docker compose up -d           # postgres + redis
uv run alembic upgrade head    # migrations (a partir da história 2)
```

Frontend (a partir da história 17):

```bash
cd frontend && npm ci && npx tsc --noEmit && npm run lint
```

---

## Estrutura

```
backend/
├── config.py        # Settings + fail-closed do trading mode
├── collection/      # layer 1: news, twitter, market data (MT5)
├── analysis/        # layer 2: sentiment, technical, signal fusion
├── execution/       # layer 3: risk manager (gate), order manager, position tracker
├── learning/        # layer 4: outcomes, weight optimizer, backtester, promotion gate
├── api/             # layer 5: FastAPI REST + WS
├── models/          # SQLAlchemy
├── migrations/      # Alembic
└── tests/
frontend/            # Next.js (história 17)
```

Fluxo: `collection → analysis → signal → risk_manager → order_manager → outcome → learning`.

---

## Convenções

- Python 3.12, `from __future__ import annotations` no topo.
- Imports absolutos a partir de `backend.` — o `pythonpath` do pytest é a raiz do repo.
- Ponytail: stdlib > dependência existente > implementação própria. Indicador técnico vem da lib
  `ta`; não reimplementar RSI/MACD/Bollinger/ATR.
- Docstrings e comentários em português; identificadores em inglês.
- Testes: `backend/tests/test_<modulo>.py`. Markers `integration` (exige Postgres/Redis) e `mt5`
  (exige terminal MT5) — o CI roda os dois serviços, mas nunca conecta em broker.
- Migrations são aditivas. Nunca editar migration já aplicada.

---

## Gotchas

- `MetaTrader5` é **win32-only**: o marker `sys_platform == 'win32'` no pyproject mantém o CI
  (Ubuntu) verde. Código que importa MT5 deve tolerar a ausência do pacote em import-time e
  falhar apenas na conexão.
- Docker não está instalado nesta máquina de desenvolvimento; testes marcados `integration`
  são pulados localmente e rodam no CI.
- `get_settings()` é memoizado com `lru_cache`. Em teste, use `Settings(_env_file=None, ...)`
  ou chame `get_settings.cache_clear()`.
- `ruff` roda com `S` (bandit): senhas em fixture precisam de `# noqa: S105/S106` ou do
  per-file-ignore já configurado para `backend/tests/`.

---

## Definition of Done (por história)

- `pytest` passa, coverage ≥ 80% no módulo tocado
- `ruff` + `mypy` limpos
- CI verde
- Commit granular e descritivo
- `AGENTS.md` e `progress.txt` atualizados
- P0 = 0, P1 = 0
