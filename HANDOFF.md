# HANDOFF — forex-bot

Passagem de contexto. Escrito para que uma sessão **com contexto limpo** continue
sem reler histórico de conversa.

**Data:** 2026-08-06 · **Branch:** `feat/forex-bot` · **Remote:** `github.com/millennium42/forex-bot`

---

## 1. Estado agora

| | |
|---|---|
| Histórias | **38 de 46 concluídas**; abertas: 39–46 |
| Conta | MetaQuotes-Demo, login 110657657, equity **US$ 100.959,21** |
| Hoje | 311 outcomes, líquido **+US$ 1.036,81**, win rate 86,8% |
| Base | 8.338 signals · 437 trades · 0 posições abertas |
| Suíte | exit 0, coverage ~93%, ~60s |

**Serviços:** API, Celery worker, Celery beat, frontend e Docker **de pé**.
O **bot está parado** — religue com o comando da §3.

---

## 2. Configuração vigente (`.env`)

```
TRADING_MODE=demo                 CYCLE_INTERVAL_SECONDS=33
TRADING_SYMBOLS=EURUSD,GBPUSD,AUDUSD,NZDUSD
SENTIMENT_ENABLED=false           MIN_SIGNAL_CONFIDENCE=0.07
SIGNAL_REPEAT_COOLDOWN_MINUTES=2  VOLUME_MAX_PER_ORDER_LOTS=2.0
MAX_DAILY_LOSS_PCT=5.0            MAX_DRAWDOWN_FROM_PEAK_PCT=10.0
```

**Só estes 4 pares.** São os únicos com USD como moeda de cotação, igual à moeda
da conta. Nos outros 122 que o broker oferece, o risco sai calculado na moeda de
cotação e é comparado com um teto na moeda da conta, **sem conversão** — o número
comparado não seria risco real. Não amplie a lista sem converter a moeda.

---

## 3. Como subir

**Script único** — depois de ligar o PC, abrir o Docker Desktop e logar no MT5:

```bash
cd C:\Users\Admin\Documents\Projetos\forex-bot; .\scripts\start_all.ps1
```

Verifica Docker/`.env`/MT5, sobe Postgres+Redis, aplica migrations e abre 5
janelas (API, Frontend, Celery Worker, Celery Beat, Bot). `-SkipBot` sobe só a
infraestrutura sem iniciar o BotRunner.

Manual, um terminal para cada (equivalente ao que o script automatiza):

```bash
cd C:\Users\Admin\Documents\Projetos\forex-bot; uv run uvicorn backend.api.main:app --host 127.0.0.1 --port 8001
```

```bash
cd C:\Users\Admin\Documents\Projetos\forex-bot\frontend; npm run dev
```

```bash
cd C:\Users\Admin\Documents\Projetos\forex-bot; uv run celery -A backend.celery_app worker -l info -P solo
```

```bash
cd C:\Users\Admin\Documents\Projetos\forex-bot; uv run celery -A backend.celery_app beat -l info
```

```bash
cd C:\Users\Admin\Documents\Projetos\forex-bot; uv run python scripts/run_bot.py
```

Auditar o banco ao vivo:

```bash
cd C:\Users\Admin\Documents\Projetos\forex-bot; uv run python scripts/auditar_db.py --follow
```

---

## 4. Armadilhas do ambiente

Todas custaram tempo real. Leia antes de debugar.

1. **`localhost` trava a conexão com o Postgres.** Docker Desktop no Windows
   anuncia `[::]:5432` mas não completa o handshake em IPv6, **sem timeout**.
   Sintoma: `alembic upgrade` e testes `integration` congelando sem nenhuma
   conexão em `pg_stat_activity`. Use sempre `127.0.0.1`.

2. **Suíte acima de ~2 min significa Docker parado**, não suíte lenta. Os testes
   `integration` ficam esperando timeout de conexão. Rode `docker compose up -d`.
   Com tudo de pé são ~60s.

3. **`Start-Process cmd.exe /c "..."` morre junto com a sessão PowerShell.**
   Serviços subidos assim caem sozinhos. Use `Start-Process` direto no executável
   com `-RedirectStandardOutput`.

4. **PowerShell 5.1 lê UTF-8 sem BOM como ANSI.** `Get-Content -Raw` seguido de
   `Set-Content` duplo-encoda acentos. Use `[System.IO.File]::ReadAllText($p,
   [Text.Encoding]::UTF8)` e `WriteAllText` com `UTF8Encoding($false)`. Para
   desfazer um duplo-encode já gravado, reinterprete via **cp1252**, não latin1.

5. **Reiniciar o bot não basta.** O `position_tracker` roda no **worker Celery**;
   mudança no `mt5_client` ou no tracker exige reiniciar o worker também. Isso já
   fez um fix parecer não ter funcionado.

6. **`MetaTrader5` é win32-only.** O marker `sys_platform == 'win32'` mantém o CI
   (Ubuntu) verde. Módulo que toque MT5 importa tardiamente e falha só na conexão.

---

## 5. Invariantes — violar é P0

1. **`TRADING_MODE=demo` é o default e falha fechado.** Real exige
   `TRADING_MODE=real` **e** `REAL_TRADING_UNLOCKED=true` juntos. Lógica só em
   `config.py::effective_trading_mode`. Segunda barreira em `MT5Client.connect()`,
   que cruza isso com o `trade_mode` real da conta.
2. **`risk_manager` é o único caminho para uma ordem.**
3. **`audit_log` é append-only**, por trigger no Postgres.
4. **Sem mocks em produção.** MT5 offline → o sistema para.
   Corolário: **nunca devolva 0.0 no lugar de dado ausente.** Zero é valor
   legítimo; o consumidor não distingue "sem dinheiro" de "sem conexão". Use `None`.
5. **`client_request_id` obrigatório**, `UNIQUE` no banco.
6. **Sem credenciais e sem PII em log.**
7. **Migrations aditivas.** Nunca editar revisão aplicada.

---

## 6. O que falta — histórias 39 a 46

Rode o Ralph:

```bash
cd C:\Users\Admin\Documents\Projetos\forex-bot; .\scripts\ralph\ralph.ps1 12
```

| # | História |
|---|---|
| 39 | Arquitetura de estratégias paralelas (`Signal.strategy`, cooldown por estratégia, isolamento de exceção) |
| 40 | **BBRSI** — Bollinger(500, 2.0) + RSI(7), reversão à média |
| 41 | **3MACD** — MACD(5,8) + (13,21) + (34,144), seguidora de tendência |
| 42 | **2MACDSTO** — MACD(13,21) + (34,144) + Stochastic(7,3,3) |
| 43 | Performance por estratégia no dashboard |
| 44 | Teto de posições simultâneas (`MAX_OPEN_POSITIONS=12`) |
| 45 | Perda e ganho como valor monetário fixo |
| 46 | Volume proporcional ao montante e à confiança |

As regras das estratégias 40–42 foram extraídas do código-fonte de
[geraked/metatrader5](https://github.com/geraked/metatrader5) e estão descritas
condição a condição em `prd.json`.

### Sobre a 45 e a 46 (pedido do operador)

**45 — alvos monetários.** SL e TP deixam de ser multiplicadores de ATR e viram
valores: perder no máximo **US$ 100**, ganhar **US$ 33,33** (100/3). O ATR ainda
define a *distância*; o *volume* é que calibra quanto essa distância vale.

> RR = 0,333 → **75% de win rate para breakeven**. É menos exigente que o RR 0,2
> anterior (83,3%). O win rate observado hoje é 86,8%.

**46 — volume por montante e confiança.** Calcula o orçamento de risco da conta
(`ACCOUNT_RISK_BUDGET_PCT`, default 12% do equity), divide por
`MAX_OPEN_POSITIONS` (12) para achar o risco por slot, e modula pela confiança do
sinal — com piso (`MIN_CONFIDENCE_VOLUME_FACTOR=0.3`) para que sinal fraco opere
com tamanho reduzido em vez de não operar. O volume cresce junto com o equity, e
os 12 trades sempre cabem no orçamento. É o "sem descarregar a carga toda no
primeiro trade".

---

## 7. Sentimento: desligado por decisão

`SENTIMENT_ENABLED=false`. A decisão é **puramente técnica**. Desligado, o runner
nem consulta documento nem carrega o analisador, e o `Signal` grava sentimento
nulo — não zero forjado.

**Consequência conhecida:** `fuse_signals` degrada a confiança quando falta uma
ponta, então o teto de confiança volta a ser o peso técnico (0,7). Foi por isso
que `MIN_SIGNAL_CONFIDENCE` caiu de 0,1 para 0,07 — com o limiar antigo, **100%
dos sinais eram bloqueados**.

O código do sentimento continua no repositório e testado. É feature futura, não
dívida.

---

## 8. Bugs encontrados auditando produção

Documentados porque a classe de erro tende a repetir.

- **Exposição em percentual comparada como monetário** (P0). O teto de 3% só
  dispararia com ~30.000% de exposição real — o limite nunca existiu. Achado por
  revisão adversarial. Hoje nenhum percentual cruza a fronteira do `risk_manager`,
  e o nome do parâmetro carrega a unidade (`current_exposure_monetary`).
- **Timestamps do MT5 tratados como UTC.** O broker devolve hora do **servidor**
  (MetaQuotes-Demo = GMT+3). A duração mínima de 239 outcomes era 3h04 quando os
  trades duravam minutos. `connect()` agora mede o offset e normaliza.
- **Perda flutuante não contava** no limite diário — só o realizado, gravado no
  fechamento. O limite de 5% só era avaliado depois da perda já realizada.
- **`mt5_position_id` guardava o `deal` id**, que não casa com `positions_get()`.
  A reconciliação nunca achava a posição.
- **`contract_size` nunca sincronizado** do broker; ficava em 100.000 fixo.
- **Cooldown de 15 min com ciclo de 33s** bloqueava ~27 ciclos seguidos: 290
  bloqueios por hora contra 18 ordens. Agora 2 min.
- **`audit_log` gravava uma linha por ciclo** enquanto o bloqueio persistisse —
  ~7.000 linhas/dia num log append-only. Agora só grava quando o motivo muda.

---

## 9. Metodologia Ralph

Uma história por iteração, cada uma em instância fresca. A memória **não** é
contexto de conversa: é `git log` + `progress.txt` + `AGENTS.md` + `prd.json`.

| Arquivo | Papel |
|---|---|
| `prd.json` | Histórias e status. Fonte da verdade do que falta. |
| `progress.txt` | Aprendizados, append-only. `Codebase Patterns` no topo. |
| `AGENTS.md` | Padrões, gotchas, onde as coisas estão. |
| `CLAUDE.md` | Invariantes e comandos. |
| `scripts/ralph/prompt.md` | Instruções de cada iteração. |

**DoD:** ruff + ruff format + mypy strict + pytest com coverage ≥ 80%, commit
granular, `AGENTS.md` e `progress.txt` atualizados, `passes: true`. Nunca marcar
`passes: true` com verificação vermelha.

### Como o Ralph falha

Três modos observados, todos já mitigados — mas verifique se reaparecerem:

1. **Deixa trabalho staged sem commit.** Antes de confiar em `passes: true`, rode
   `git status`. Foi assim que apareceu um `runner.py` com equity fictício.
2. **Joga a suíte em background e encerra o turno esperando notificação.** O
   `prompt.md` agora manda rodar em primeiro plano com timeout de 600s.
3. **Cria arquivos-lixo** por redirecionamento de shell mal formado (`float`,
   `list[str]`, `SentimentScore`, `frontend/([])`). Limpe com `git clean` ou
   remova à mão antes de commitar.

Regra de permissão que já travou o loop: `ask` para `Bash(git commit)` no
`~/.claude/settings.json` faz o agente headless esperar aprovação para sempre.
Hoje `git commit` está em `allow` e só `git push` pede confirmação.

---

## 10. Avaliação honesta da estratégia

```
hoje: 311 outcomes · líquido +US$ 1.036,81 · win rate 86,8%
```

Está lucrando. Mas o número que decide não é o win rate — é a **margem sobre o
breakeven**. Com o RR atual (0,2) o breakeven é 83,3% e você entrega 86,8%: são
**3,5 pontos de folga**. Cada perda vale ~5 ganhos, então uma sequência ruim
consome a folga rápido.

A história 45 melhora isso: RR 0,333 baixa o breakeven para 75%, dando ~12 pontos
de folga com o mesmo win rate.

**O que ainda não foi validado:** os outcomes anteriores ao fix de timezone têm
duração inflada em 3h e não são comparáveis com os novos. E o `weight_optimizer`
existe mas **não está agendado** — nenhum peso foi ajustado até agora. As
estratégias paralelas (39–42) é que vão dar o dado para decidir qual abordagem
funciona.

---

## 11. Aviso

Isto é engenharia de software, não recomendação de investimento. O perfil de
risco atual é **agressivo por decisão explícita do operador**: os tetos de risco
por trade e de exposição agregada foram removidos, e o único limite de tamanho é
a margem do broker mais o teto de lotes por ordem. Kill switch de perda diária e
drawdown do pico continuam ativos. O bot nunca opera conta real por decisão
própria; a promoção demo→real é manual e registrada em `audit_log`.
