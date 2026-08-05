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
| 6–20 | ⏳ pendentes |

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
