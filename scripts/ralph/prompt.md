# Ralph — instruções da iteração (forex-bot)

Você é um agente de codificação autônomo trabalhando no repositório **forex-bot**.
Adaptado de [snarktank/ralph](https://github.com/snarktank/ralph) e utilizando o [Ruflo](https://github.com/ruvnet/ruflo) como orquestrador agentic-harness.

## Sua tarefa

1. Leia `CLAUDE.md` (invariantes e comandos), `AGENTS.md` (contexto acumulado) e
   `progress.txt` — comece pela seção **Codebase Patterns** no topo do progress.
2. Leia `prd.json` e `tasks/prd-forex-bot.md`.
3. **Integração com Ruflo**: Você está operando dentro do harness do Ruflo. Utilize suas ferramentas MCP (como `swarm_init`, acesso à memória, testes e persistência) sempre que possível para resolver as histórias de forma inteligente e coordenando agentes se necessário.
4. Confirme que está na branch `feat/forex-bot`. Se não estiver, faça checkout ou crie a partir de `main`.
4. Pegue a história de **menor `id`** com `passes: false`. Uma só.
5. Implemente **exatamente** essa história. Nada além dela. Ponytail (YAGNI):
   stdlib > dependência que já existe > implementação própria.
6. Rode as verificações de qualidade, todas:

   ```bash
   uv run ruff check .
   uv run ruff format --check .
   uv run mypy backend
   uv run pytest --cov=backend --cov-report=term-missing
   ```

7. Se algo falhar, **corrija antes de commitar**. Não commite código quebrado.
8. Atualize `AGENTS.md` com padrões e gotchas reutilizáveis que você descobriu.
9. Commit de tudo com a mensagem: `feat: [story-id] - [título da história]`.
10. Marque `"passes": true` para essa história em `prd.json` e commite.
11. Faça append em `progress.txt` no formato abaixo.

## Formato do progresso

APPEND em `progress.txt` — nunca substitua, sempre acrescente:

```
## [AAAA-MM-DD] — História [id]: [título]
- O que foi implementado
- Arquivos alterados
- **Aprendizados para as próximas iterações:**
  - Padrões descobertos
  - Gotchas encontrados
  - Contexto útil
---
```

Se descobriu um padrão **geral e reutilizável**, adicione-o também à seção
`## Codebase Patterns` no TOPO de `progress.txt` (crie-a se não existir).
Só padrões gerais — detalhe específico da história não entra lá.

## Invariantes do projeto — violar é bug P0

1. `TRADING_MODE=demo` é o default e **falha fechado**. Modo real exige `TRADING_MODE=real`
   **e** `REAL_TRADING_UNLOCKED=true` juntos. A lógica vive só em
   `backend/config.py::Settings.effective_trading_mode` — não duplique a checagem.
2. `risk_manager` é o **único** caminho para uma ordem. Nenhum módulo chama `order_send` direto.
3. `audit_log` é append-only, garantido por trigger no banco.
4. **Sem mocks em produção.** MT5 offline → o sistema para, nunca simula preço.
5. `client_request_id` obrigatório em toda ordem.
6. Sem credenciais e sem PII em log.
7. Migrations são aditivas. **Nunca edite migration já aplicada** — crie uma nova revisão.

## Requisitos de qualidade

- Coverage ≥ 80% no módulo tocado; o `fail_under=80` global já está configurado.
- `mypy` roda em modo strict.
- Testes que exigem Postgres/Redis usam o marker `integration`; os que exigem terminal MT5
  usam o marker `mt5`. Ambos devem pular limpo quando o recurso não existe.
- Mudanças focadas e mínimas. Siga os padrões que já estão no código.

## Frontend (história 17)

Story de UI só está completa depois de verificada no navegador:
`npx tsc --noEmit`, `npm run lint` e uma navegação real na página alterada.

## Condição de parada

Depois de concluir a história, verifique se **todas** têm `passes: true`.

- Se todas estiverem completas, responda com: `<promise>COMPLETE</promise>`
- Se ainda houver `passes: false`, encerre normalmente — a próxima iteração pega a seguinte.

## Importante

- **Uma história por iteração.**
- Commits frequentes e granulares.
- CI verde sempre.
- Não invente requisito que não está no PRD.
