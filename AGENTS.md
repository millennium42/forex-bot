# AGENTS.md

Contexto acumulado para a próxima iteração do Ralph. Atualizado a cada história.

---

## Estado atual

| História | Status |
|---|---|
| 1 — Scaffold do repo | ✅ |
| 2–20 | ⏳ pendentes |

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
