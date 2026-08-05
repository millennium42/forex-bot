# AGENTS.md

Contexto acumulado para a próxima iteração do Ralph. Atualizado a cada história.

---

## Estado atual

| História | Status |
|---|---|
| 1 — Scaffold do repo | ✅ |
| 2 — Modelos + migrations | ✅ |
| 3 — Conector MT5 | ✅ |
| 4 — News collector | ✅ |
| 5 — Twitter collector | ✅ |
| 6 — Sentiment analyzer | ✅ |
| 7 — Technical analyzer | ✅ |
| 8 — Signal fusion | ✅ |
| 9 — Risk manager | ✅ |
| 10 — Order manager | ✅ |
| 11 — Position tracker | ✅ |
| 12 — Outcome recorder | ✅ |
| 13 — Weight optimizer | ✅ |
| 14 — Backtester | ✅ |
| 15 — Promotion gate | ✅ |
| 16–20 | ⏳ pendentes |

---

## Padrões estabelecidos

- **Pacote raiz `backend/`**, importado como `backend.<camada>.<modulo>`. `pythonpath = ["."]`
  no `pyproject.toml` faz o pytest resolver isso sem instalar o pacote.
- **`from __future__ import annotations`** no topo de todo módulo — mypy strict + Python 3.12.
- **Config centralizada** em `backend/config.py`. Nenhum módulo lê `os.environ` direto.
- **Fail-closed do trading mode** vive só em `Settings.effective_trading_mode`. Consumidores usam
  `settings.is_real_trading`; não reimplementar a checagem.
- **Docstrings/comentários em português, identificadores em inglês.**
- **Gerenciador de pacotes: `uv`.** Não usar pip/poetry.
- **Persistência da coleta é uma só:** `backend/collection/documents.py::store_items` grava
  qualquer item que satisfaça o Protocol `CollectedItem` (`.dedupe_hash` + `.to_document()`).
  Coletor novo implementa o Protocol; não reescreve o insert com dedupe.
- **Coletor é best-effort** (ao contrário do MT5, que falha fechado): fonte indisponível é
  logada e pulada. Só o pipeline que dimensiona ordem não tolera visão parcial.
- **Dependência pesada entra por Protocol + loader de módulo.** `MT5Terminal`, `SentimentBackend`
  e `CacheClient` seguem o mesmo formato: Protocol na assinatura, import dentro de uma função
  `_load_*` no nível do módulo. O teste faz `monkeypatch.setattr(modulo, "_load_x", ...)`; o
  runtime carrega o pacote de verdade. Nenhum teste do CI chega a baixar modelo ou abrir terminal.
- **Faixa numérica é garantida na construção, não por convenção.** `SentimentScore` faz clamp no
  `__post_init__`, então nem o modelo nem uma entrada de cache adulterada produzem valor fora de
  [-1,1] / [0,1]. Vale para os scores das próximas histórias (técnico, fusion).
- **Ausência de informação é `confidence=0`, nunca um score inventado.** Texto vazio, rótulo
  desconhecido, cache morto, indicador em aquecimento: todos devolvem score 0 com confiança 0.
  Quem consome pondera pela confiança, então o sinal simplesmente não pesa.
- **Score e confiança são coisas diferentes.** O score diz a direção; a confiança diz se ela foi
  sustentada. No técnico, `confiança = concordância entre componentes × intensidade média` — dois
  indicadores brigando derrubam a confiança mesmo com score alto. O fusion (história 8) pondera
  por ela.
- **Indicador em unidade de preço é normalizado por ATR** antes de virar componente. Sem isso, um
  par volátil dominaria o score só por oscilar mais.
- **Pesos de combinação vivem só no `signal_fusion` (história 8).** O `technical_analyzer` usa
  pesos fixos e iguais de propósito — dois lugares versionando peso seriam duas fontes de verdade.

---

## Gotchas descobertos

- `MetaTrader5` só instala em Windows. O marker `sys_platform == 'win32'` no `pyproject.toml`
  mantém o CI (Ubuntu) funcional. Qualquer módulo que importe MT5 precisa sobreviver ao import
  em Linux — importar dentro da função ou tratar `ImportError` — e falhar só na conexão.
- `transformers`/`torch` estão no extra `nlp`, não no core: ~2GB. O sentiment analyzer (história 6)
  precisa de fallback quando o extra não está instalado.
- Docker não está instalado na máquina de desenvolvimento atual. Testes que exigem Postgres/Redis
  devem usar o marker `integration` e ser puláveis localmente.
- `ruff` está com o ruleset `S` (bandit). Strings tipo senha em teste disparam S105/S106 —
  `backend/tests/*` já tem per-file-ignore.
- `get_settings()` é `lru_cache`. Em teste, instancie `Settings(_env_file=None, **overrides)`
  para não vazar o `.env` local para dentro da asserção.
- **Dois níveis de teste de banco.** Fixture `session` = SQLite in-memory (constraints portáveis,
  roda em qualquer máquina); fixture `pg_engine` = Postgres real via Alembic, sob o marker
  `integration`, pulado quando o banco não responde em 3s. Trigger, enum nativo e JSONB só são
  exercitados no segundo nível.
- SQLite ignora foreign keys por default — o `conftest.py` liga `PRAGMA foreign_keys=ON` por
  listener. Sem isso, teste de FK passa de mentira.
- SQLite só autoincrementa `INTEGER PRIMARY KEY`: use
  `BigInteger().with_variant(Integer(), "sqlite")` em PK grande.
- Um mesmo tipo enum usado em duas tabelas (`direction` em `signals` e `outcomes`) precisa, na
  migration, de `postgresql.ENUM(..., create_type=False)` + `.create(bind, checkfirst=True)`
  antes dos `create_table` — senão o segundo falha com "type already exists".
- `ruff` reclama de `type_annotation_map` como mutável de classe: anote com `ClassVar[dict[Any, Any]]`.
- **Docker Desktop no Windows: use `127.0.0.1`, nunca `localhost`.** O `localhost` resolve para
  `::1`; o proxy do Docker anuncia `[::]:5432` mas não completa o handshake, e a conexão fica
  pendurada sem timeout — `alembic upgrade` e os testes `integration` travam indefinidamente.
  Todas as URLs de banco/Redis usam `127.0.0.1`.
- `jq` **não** está instalado e o winget falhou nele. Os scripts do Ralph leem `prd.json` via
  Python (`uv run python -c`), sem jq.
- `httpx.MockTransport` cobre também `client.stream(...)`: basta devolver
  `httpx.Response(200, content=b"...")` e o `iter_lines()` funciona. Não é preciso servidor de
  teste para exercitar o filtered stream do Twitter.
- Task Celery não expõe `.queue`. Para asserir a fila de destino sem broker, use
  `app.amqp.router.route({}, "nome.da.task")["queue"].name`.
- `ruff` com o ruleset `RUF` inclui **RUF100 (noqa inútil)**. Colocar `# noqa: BLE001` num
  `except Exception` vira erro, porque `BLE` não está no `select`. Não anote o que não é regra.
- **RUF002**: caractere ambíguo em docstring (`×`, aspas curvas tipográficas) é erro de lint.
  Acentos normais passam; símbolo matemático unicode, não.
- Módulo novo que importa pacote fora do core precisa entrar em `[[tool.mypy.overrides]]` com
  `ignore_missing_imports` — senão o mypy quebra na máquina que não tem o extra (`transformers.*`
  foi adicionado na história 6).
- Teste `integration` que grava em Redis precisa de **chave única por execução** (`uuid4()` no
  texto). Com chave fixa e TTL longo, a segunda rodada da suíte lê a entrada da rodada anterior e
  a asserção de "modelo chamado uma vez" passa por acidente — ou falha, dependendo da ordem.
- `pandas` não traz tipos: sem `pandas-stubs` (já no extra `dev`) o mypy strict quebra com
  `import-untyped`. Com os stubs instalados, `pd.isna(float)` é tipado como sempre falso e o
  `warn_unreachable` acusa o corpo do `if` — use `math.isnan` para checar NaN de escalar.
- Série OHLC constante deixa o **RSI indefinido** (nem ganho, nem perda → NaN). Fixture de teste
  "plana" não exercita o caminho feliz dos indicadores; exercita o caminho de NaN.

---

## Onde estão as coisas

| O quê | Onde |
|---|---|
| Regras de risco (valores) | `backend/config.py` |
| Invariantes do projeto | `CLAUDE.md` |
| Histórias e critérios | `prd.json` / `tasks/prd-forex-bot.md` |
| Infra de estado | `docker-compose.yml` (só Postgres + Redis) |
| CI | `.github/workflows/ci.yml` |

---

## Decisões de arquitetura

- **A API e os workers não são containerizados.** O conector MT5 precisa do terminal nativo no
  mesmo host (socket 127.0.0.1); containerizar quebraria o acesso. O Compose provê apenas
  Postgres e Redis.
- **O job `frontend` do CI só nasce na história 17**, junto com o diretório. Job que não tem o que
  rodar é ruído.
- **Ralph + Ruflo:** O Ralph Loop (`scripts/ralph/ralph.ps1`) itera pelas histórias pendentes. O agente (Claude) foi instruído a usar ferramentas e swarms do Ruflo para auxiliar nas tarefas complexas, mas o Ruflo e o Ralph não anulam as invariantes de fail-closed e rulesets exigidos no projeto. Se a instalação do Ruflo via `npx` falhar por limites de memória, recomenda-se instalar os plugins no Claude.
- **Mypy e SQLAlchemy Models em Testes:** Em testes, afirmar  ssert obj.atributo is True estreita o tipo (narrowing) para Literal[True]. Se uma funcao atualiza o banco e desativa o atributo, o mypy vai reclamar de 'unreachable code' em qualquer codigo apos  ssert not obj.atributo se voce nao limpar a hipotese do compilador. Faca session.refresh(obj) antes do assert reverso.
- **PowerShell em scripts npm/npx:** Evite usar `&&` em execuções de PowerShell (como no `run_command` do Windows). Use `;` ou os divida em etapas.
- **Next.js e Recharts:** Cuidado com regras de eslint como `react-hooks/set-state-in-effect`. O uso de `useEffect` para marcar `mounted = true` pode conflitar se não anotado adequadamente ou caso haja separação clara de server/client.
- **TestClient com SQLite In-Memory:** O SQLite in-memory cria um banco por conexão (thread). Para compartilhar a mesma instância em testes de API que usam TestClient (FastAPI spawns threads), use `create_engine` com `poolclass=StaticPool` e `connect_args={"check_same_thread": False}`.
