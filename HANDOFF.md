# HANDOFF — forex-bot

Documento de passagem de contexto. Escrito para que uma sessão **com contexto
limpo** consiga continuar sem reler o histórico da conversa.

**Data:** 2026-08-05
**Branch de trabalho:** `feat/forex-bot` (mergeada em `main`)
**Remote:** `https://github.com/millennium42/forex-bot.git`

---

## 1. Onde o projeto está

As 20 histórias do PRD original estão **concluídas e commitadas**. O pipeline
completo existe e está verde:

```
collection → analysis → signal → risk_manager → order_manager → outcome → learning
```

| Verificação | Estado |
|---|---|
| `uv run ruff check .` | limpo |
| `uv run ruff format --check .` | limpo |
| `uv run mypy backend` (strict) | limpo, 74 arquivos |
| `uv run pytest` | exit 0, coverage **86%** (mínimo exigido: 80%) |
| Testes `integration` (Postgres) | rodam de verdade, zero skip |

O que **não** está pronto está em `prd.json` como histórias 21–23, abertas.

---

## 2. Como subir tudo

```bash
cd C:\Users\Admin\Documents\Projetos\forex-bot

# 1. Infra de estado
docker compose up -d              # postgres + redis

# 2. Dependências
uv sync --extra dev

# 3. Schema
uv run alembic upgrade head

# 4. API (terminal 1)
uv run uvicorn backend.api.main:app --reload --port 8001

# 5. Dashboard (terminal 2)
cd frontend && npm run dev        # http://127.0.0.1:3000

# 6. Motor do bot (terminal 3) — exige MetaTrader 5 aberto
uv run python scripts/run_bot.py --cycles 1    # dry run, um ciclo
uv run python scripts/run_bot.py               # contínuo
```

Worker Celery (coletores de notícia e tweet):

```bash
uv run celery -A backend.celery_app worker -l info -P solo
```

> `-P solo` é obrigatório no Windows: o pool padrão (prefork) não funciona lá.

---

## 3. Armadilhas do ambiente — leia antes de debugar

Estas três já custaram horas. Estão aqui para não custarem de novo.

1. **`localhost` trava a conexão com o Postgres.** Docker Desktop no Windows
   anuncia `[::]:5432` mas não completa o handshake em IPv6, e a conexão fica
   pendurada **sem timeout**. O sintoma é `alembic upgrade head` e os testes
   `integration` congelando, sem nenhuma conexão aparecendo em
   `pg_stat_activity`. **Use sempre `127.0.0.1`.**

2. **PowerShell 5.1 lê UTF-8 sem BOM como ANSI.** `Get-Content -Raw` seguido de
   `Set-Content` duplo-encoda todos os acentos. Use
   `[System.IO.File]::ReadAllText($p, [Text.Encoding]::UTF8)` e `WriteAllText`
   com `UTF8Encoding($false)`. Para desfazer um duplo-encode já gravado,
   reinterprete via **cp1252**, não latin1 — latin1 quebra travessão e aspas curvas.

3. **`MetaTrader5` é win32-only.** O marker `sys_platform == 'win32'` no
   `pyproject.toml` mantém o CI (Ubuntu) verde. Qualquer módulo que toque MT5
   precisa importar tardiamente e falhar só na conexão, nunca no import.

---

## 4. Invariantes — violar é P0

Estes são os compromissos que o código inteiro assume. Não os relaxe sem
mudar o PRD primeiro.

1. **`TRADING_MODE=demo` é o default e falha fechado.** Modo real exige
   `TRADING_MODE=real` **e** `REAL_TRADING_UNLOCKED=true` ao mesmo tempo.
   A lógica vive só em `backend/config.py::Settings.effective_trading_mode`.
   Há uma segunda barreira em `MT5Client.connect()`, que cruza isso com o
   `trade_mode` real da conta e derruba a sessão no mismatch.
2. **`risk_manager` é o único caminho para uma ordem.** Nada chama `order_send`
   fora de `execution/order_manager.py`.
3. **`audit_log` é append-only**, garantido por trigger no Postgres (UPDATE,
   DELETE e TRUNCATE bloqueados) — não por convenção de código.
4. **Sem mocks em produção.** MT5 offline → o sistema para. Nunca simula preço.
   Corolário aprendido na prática: **nunca devolva 0.0 no lugar de um dado
   ausente.** Zero é um valor legítimo e o consumidor não consegue distinguir
   "sem dinheiro" de "sem conexão". Use `None`.
5. **`client_request_id` obrigatório em toda ordem.** É a chave de idempotência,
   com `UNIQUE` no banco.
6. **Sem credenciais e sem PII em log.**
7. **Migrations são aditivas.** Nunca edite uma revisão já aplicada.

---

## 5. Correções aplicadas nesta sessão

Três problemas que o loop autônomo deixou passar e foram corrigidos à mão:

1. **`runner.py` alimentava o risk manager com ficção** (P0). Tinha
   `equity=20.0` fixo, `current_exposure=0.0`, `daily_loss=0.0` e stop loss a
   100 pontos constantes — ou seja, as regras de 1%/3%/5% eram avaliadas sobre
   números inventados e o risk manager não protegia nada. Agora tudo vem do
   broker e do banco, o stop é ATR-based, e o volume é sempre o lote mínimo.
2. **`/system/account` devolvia `balance=0.0` quando o MT5 estava offline**,
   engolindo a exceção com `try/except/pass`. Agora usa `MT5Client`, devolve
   `None` e loga o motivo.
3. **Dubles de teste desatualizados**: o `MT5Terminal` Protocol ganhou
   `copy_rates_from_pos` nas histórias 7 e 14, mas os `FakeTerminal` ficaram
   para trás e o mypy strict recusava a injeção.

---

## 6. Backlog

**23/23 histórias concluídas.** `prd.json` está sem nada em aberto.

Para retomar com histórias novas, acrescente-as ao `prd.json` e rode:

```bash
.\scripts\ralph\ralph.ps1 5        # Windows
./scripts/ralph/ralph.sh 5         # Git Bash / Linux
```

Melhorias identificadas mas **não** transformadas em história (ninguém pediu):

- O `runner` avalia todos os símbolos a cada ciclo sem paralelismo. Com muitos
  pares o ciclo alonga linearmente.
- O sentimento entra como `None` no `fuse_signals` do runner — os coletores
  gravam documentos, mas o runner ainda não puxa o score correspondente.
  Hoje a decisão é puramente técnica.
- Não há reconciliação automática agendada; `position_tracker` existe mas
  precisa ser acionado por um beat do Celery.

---

## 7. Especificação do dashboard (história 22)

O dashboard atual é funcional mas mínimo. O pedido é um painel operacional de
verdade. O backend já expõe o que precisa (`/trades`, `/signals`, `/system/*`,
`/promotion`, WebSocket de eventos).

**Abas:**

| Aba | Conteúdo |
|---|---|
| Visão geral | Equity curve, KPIs (win rate, profit factor, drawdown, Sharpe), status da conta e do kill switch |
| Trades | Tabela com filtro por símbolo/status/período; clique abre modal com o trade e o sinal que o originou |
| Sinais | Sinais recentes com score de sentimento vs. técnico e a decisão resultante |
| Gates | Os 5 critérios de promoção demo→real, cada um com valor atual, alvo e distância |
| Auditoria | Stream do `audit_log`, filtrável por tipo de evento |

**Gráficos:** equity curve (área), distribuição de P&L (histograma),
win rate por par (barras), sentimento vs. técnica ao longo do tempo (linhas
sobrepostas), heatmap de resultado por hora do dia.

**Modais:** detalhe do trade (entrada, SL, TP, sinal originador, outcome),
detalhe do sinal (indicadores crus, pesos aplicados, versão dos pesos),
confirmação do kill switch.

**Insights** — cada card responde uma pergunta, não só mostra número:
"seu win rate cai 12% depois das 16h", "trades contra o sentimento perdem 2x
mais", "a versão de pesos v3 está 8% acima da v2".

**Regras que a UI precisa respeitar:**

- Quando `connected: false` no `/system/account`, mostre `—`, nunca `R$ 0,00`.
  O backend manda `None` de propósito — a UI não pode desfazer isso.
- O badge de modo (`demo`/`real`) precisa ser impossível de ignorar. Modo real
  em vermelho, permanente no header.
- Dark mode e light mode.

---

## 8. Metodologia — Ralph loop

Uma história por iteração, cada uma em instância fresca de IA. A memória entre
iterações **não** é contexto de conversa: é `git log` + `progress.txt` +
`AGENTS.md` + `prd.json`. É por isso que este handoff existe.

| Arquivo | Papel |
|---|---|
| `prd.json` | Histórias e status. Fonte da verdade do que falta. |
| `progress.txt` | Aprendizados, append-only. Seção `Codebase Patterns` no topo. |
| `AGENTS.md` | Padrões, gotchas e onde as coisas estão. |
| `CLAUDE.md` | Invariantes e comandos de verificação. |
| `scripts/ralph/prompt.md` | Instruções que cada iteração recebe. |
| `tasks/prd-forex-bot.md` | PRD completo em markdown. |

**Definition of Done por história:** ruff + ruff format + mypy strict + pytest
com coverage ≥ 80%, commit granular, `AGENTS.md` e `progress.txt` atualizados,
`passes: true` no `prd.json`. Nunca marque `passes: true` com verificação
vermelha.

**Como o Ralph erra:** ele fecha histórias e marca `passes: true` corretamente,
mas cria arquivos fora do escopo da história e os deixa **sem commit** — foi
assim que o `runner.py` com equity fictício apareceu. Antes de confiar em
`passes: true`, rode `git status` e revise o que ficou solto na working tree.

---

## 9. Aviso

Isto é engenharia de software, não recomendação de investimento. Os parâmetros
de risco são defaults conservadores para um sistema de estudo. O bot nunca
opera conta real por decisão própria; a promoção demo→real é manual, explícita
e registrada em `audit_log`. Valide você mesmo antes de qualquer capital real.
