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
| 16 — API FastAPI | ✅ |
| 17 — Dashboard Next.js | ✅ |
| 18 — Kill switch | ✅ |
| 19 — Observabilidade | ✅ |
| 20 — Hardening | ✅ |
| 21 — Testes do BotRunner | ✅ |
| 22 — Dashboard rico | ✅ |
| 23 — Lote mínimo por símbolo | ✅ |
| 24 — Runner persiste o Signal | ✅ |
| 25 — Sentimento entra na decisão | ✅ |
| 26 — Timeframe configurável, default M5 | ✅ |
| 27 — Filtro de confiança mínima calibrado | ✅ |
| 28 — P0: exposição em unidade coerente | ✅ |
| 29 — Perda flutuante conta e drawdown acumulado bloqueia | ✅ |
| 30 — Volume proporcional ao equity | ✅ |
| 31 — contract_size sincronizado com o broker | ✅ |
| 32 — Perfil agressivo: margem do broker como único teto de tamanho | ✅ |
| 33 — Stop e alvo do perfil agressivo | ✅ |
| 34 — Múltiplas posições por símbolo com leitura distinta | ✅ |
| 35 — Alpha factors clássicos no technical analyzer | ✅ |
| 36 — Dashboard simplificado e fiel ao dado | ✅ |
| 37 — Auditoria contínua do banco | ✅ |
| 38 — Perfil de lançamento: 33s, 4 pares, sem sentimento | ✅ |
| 39 — Arquitetura de estratégias paralelas | ✅ |
| 40 — Estratégia BBRSI | ✅ |

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
- **Metadado de broker por símbolo (`Instrument`) é sincronizado no ponto de uso, não só na
  criação.** `_get_or_create_instrument` (história 23) chama `client.get_symbol_info(symbol)` a
  cada ciclo e atualiza `min_volume` se o broker mudou — o registro local nunca fica desatualizado
  em relação a quem manda de verdade. Vale como padrão para qualquer outro campo de `Instrument`
  que venha do MT5 no futuro (contract_size, digits, point).
- **`OrderRequest` aceita campos de contexto opcionais (`min_volume: float | None = None`) para o
  `risk_manager` validar sem quebrar quem já constrói a request sem esse dado.** `None` pula a
  checagem em vez de rejeitar por um valor que ninguém informou — mesma filosofia de
  "ausência de informação não vira decisão", aplicada a validação de risco.
- **Limiar de decisão calibrado por dado real, nunca por número escolhido no ar.** Quando uma
  história pede "calibre X a partir da distribuição real" e a amostra ainda não existe (`n=0` ou
  `n` pequeno), o script de calibração não deve inventar um valor — ele reporta amostra
  insuficiente honestamente, e o default até lá cai no valor conservador mais próximo já
  calibrado no código (aqui, o `threshold=0.1` que `fuse_signals` já usa para decidir direção),
  documentado como provisório em `progress.txt` e no comentário do campo em `config.py`.
- **Config que representa uma constante externa mágica (timeframe do MT5, e no futuro qualquer
  outro enum do broker) entra como nome (`str`) validado por `field_validator`, com um
  `TIMEFRAME_MAP` local traduzindo para o inteiro real — nunca importa `MetaTrader5` em
  `config.py` (win32-only).** Diferente do `trading_mode`, aqui não há valor seguro para
  degradar: nome desconhecido **falha no boot** (`ValidationError`), não vira default silencioso.
  `Settings.mt5_timeframe` expõe o inteiro já traduzido; o runner nunca faz o lookup sozinho.
- **O `Signal` é gravado no `_process_symbol`, não no `_executar`.** Toda decisão da fusão —
  inclusive HOLD — precisa virar linha em `signals` para o outcome comparar previsão com
  resultado e para o filtro de confiança (história 27) ter distribuição real para calibrar. Por
  isso o instrumento (`_get_or_create_instrument`) também subiu para `_process_symbol`: até um
  HOLD precisa de `instrument_id` para o `Signal`, e `_executar` passou a receber `instrument`
  como parâmetro em vez de buscar de novo.

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
- **Congelar `datetime.now()` em teste, sob mypy strict:** o `@classmethod now(cls, tz=None)`
  precisa devolver `cls(...)`, não `datetime(...)` — devolver o tipo base quebra o `override`
  (`Return type "datetime" incompatible with return type "FixedDatetime" in supertype`).
- **`monkeypatch.setattr(modulo, "time", ...)` (ou qualquer nome só `import`ado no topo do
  módulo, sem estar em `__all__`) quebra o mypy strict com `does not explicitly export attribute`.**
  Para mockar `time.sleep` chamado de dentro de outro módulo, importe `time` no próprio teste e
  faça `monkeypatch.setattr(time, "sleep", ...)` — é o mesmo objeto de módulo, então o efeito é
  idêntico sem acessar o atributo pelo caminho não reexportado.
- **RSI e Bollinger no `technical_analyzer` são reversão à média, não seguem tendência.** Uma
  série de candles em alta forte não garante `Direction.BUY` na fusão — o RSI sobrecomprado puxa
  para venda e pode vencer o MACD (momento) dependendo da confiança relativa. Teste de integração
  ponta-a-ponta (candles → ordem) não deve fixar a direção esperada; fixe amplitude pequena (ATR
  baixo, cabe no limite de 1% de risco) e valide só que a ordem foi criada, não o lado.
- **Coluna `NOT NULL` nova numa tabela com dados existentes: `server_default` na criação, depois
  `op.alter_column(..., server_default=None)` para não deixar o default no schema permanentemente**
  (o default "de verdade" já mora no `mapped_column(default=...)` do lado Python). Sem isso, testes
  que fazem `INSERT` via SQL cru (bypassando o ORM) quebram por `NotNullViolation` — teste que
  insere `instruments` direto por `text()` precisa listar a coluna nova explicitamente, como já
  fazia com `contract_size`.
- **`MT5Terminal` é um `Protocol` estrutural: todo dublê usado como `terminal=` num teste precisa
  ganhar o método novo quando o Protocol cresce**, senão o mypy strict rejeita a injeção (ou, se o
  dublê tem `# type: ignore` na criação do `MT5Client`, o teste passa mas o comportamento faltante
  quebra em runtime se o código exercitar aquele método). Adicionar `symbol_info` ao Protocol
  (história 23) exigiu atualizar os `FakeTerminal` de `test_mt5_client.py`, `test_runner.py` e
  `test_order_manager.py` — `test_position_tracker.py` ficou de fora porque seu dublê já usa
  `# type: ignore` e não invoca esse caminho.
- **Estado monotônico persistido sem tabela dedicada: reaproveitar o `audit_log`.** O pico de
  equity (história 29) não precisa de coluna nem tabela própria — cada novo máximo grava um evento
  `EQUITY_PEAK_UPDATED` com o valor no `payload`, e como o pico só cresce, o evento mais recente
  já é o pico (`ORDER BY id DESC LIMIT 1`). O bloqueio por drawdown segue exatamente o padrão do
  kill switch (história 18): dois tipos de evento (triggered/reset), o mais recente decide o
  estado, reset só por ação manual. Ver `backend/execution/drawdown_guard.py` como referência para
  a próxima trava que precisar do mesmo formato.
- **Nenhum percentual cruza a fronteira do `risk_manager`.** Toda quantidade que entra em
  `validate_order` (exposição, risco por trade, perdas) é valor monetário na moeda da conta. O
  `risk_manager` já converte os tetos configurados em `%` (`max_total_exposure_pct` etc.) para
  monetário internamente via `equity * pct/100` — quem chama nunca deve fazer essa conversão de
  novo nem passar o percentual bruto. Nome do parâmetro deixa a unidade explícita
  (`current_exposure_monetary`, `trade_monetary_risk`), nunca só `current_exposure`. Bug real da
  história 28: comparar percentual (~1-5) com teto monetário (~milhares) fazia o teto nunca disparar.
- **Adicionar valor a um enum nativo do Postgres exige migration própria (`ALTER TYPE ... ADD
  VALUE IF NOT EXISTS`), nunca editar a migration que criou o tipo.** O SQLite dos testes de
  unidade deriva o CHECK do enum Python atual, então um valor novo em `AuditEventType` passa
  despercebido lá mesmo sem migration — só o Postgres real expõe a ausência. `ADD VALUE` funciona
  dentro da transação padrão do Alembic (Postgres 12+), desde que o valor novo não seja usado na
  mesma transação que o adiciona. Ver `7a2f9c1d4e6b_add_drawdown_audit_events.py`.
- **Volume da ordem é dimensionado pelo risco, não mais um lote fixo (história 30).**
  `BotRunner._calcular_volume` faz `volume = (equity * risco%) / (distância_sl * contract_size)`,
  arredondado **para baixo** no `volume_step` do broker (nunca para cima — isso infla o risco
  real). Se o risco configurado não paga nem o `min_volume` do broker, devolve `None` e a ordem é
  rejeitada em `_executar` — mesma filosofia de "ausência de cobertura não vira ordem
  sub-dimensionada". Um `+ 1e-9` antes do `math.floor` absorve erro de ponto flutuante da divisão
  (`6.9999999999997` precisa virar `7`, não `6`). O teto por **contagem** de trades diários
  (`MAX_TRADES_PER_DAY`) saiu de cena nesta história — quem protege agora é só o limite de perda
  (diário + drawdown do pico, histórias 18/29), não mais um número de operações.
- **`mapped_column(default=...)` só se aplica no INSERT/flush, nunca na construção do objeto
  Python em memória.** Um teste que cria `Instrument(symbol="X", contract_size=100_000.0)` sem
  `session.add()`/commit e usa o objeto na hora (ex.: passando pra uma função pura como
  `_calcular_volume`) recebe `instrument.volume_step is None`, não o default do model — precisa
  passar todos os campos usados explicitamente no construtor.
- **Sincronizar metadado de broker é "todos os campos ou nenhum", não campo a campo.** A história
  23 sincronizava `min_volume`; a 30 acrescentou `volume_step`/`volume_max` mas esqueceu
  `contract_size`, que ficava preso no default 100_000 (correto para EURUSD, uma ordem de
  grandeza errado para XAUUSD, que usa 100). A 31 corrigiu incluindo `contract_size` na mesma
  comparação `mudou = (...)` do `_get_or_create_instrument`. Ao adicionar um novo campo de
  `SymbolInfo` no futuro, incluí-lo ali é parte do trabalho, não um follow-up — o padrão já existe,
  só falta lembrar de estendê-lo.
- **Mudar um default de config usado num teste que só cita o *nome* do campo (`runner.settings.
  atr_sl_multiplier`) propaga sozinho; um teste que fixa o *valor numérico esperado* (fixture de ATR
  escolhida a dedo para o default antigo) não propaga e quebra silenciosamente.** A história 33 trocou
  `atr_sl_multiplier` de 2.0 para 1.0; `test_executar_volume_e_derivado_do_risco` (história 30) tinha
  um ATR fixo calibrado para o default antigo cair num múltiplo exato do `volume_step` do broker — com
  o novo default o volume calculado passou a arredondar (`1.6666... → 1.66`) e o `pytest.approx`
  contra o valor bruto não batia mais. Ao mudar um default numérico, procure todo teste cujo *dado de
  entrada* (não só a leitura do campo) foi escolhido em função do valor antigo.
- **Coluna `TimestampTZ` lida do banco em teste unitário (SQLite) volta naive; em Postgres volta
  aware.** Subtrair diretamente de `datetime.now(UTC)` levanta `TypeError` só no SQLite — o mesmo
  gotcha que `outcome_recorder._utc` já resolvia para `opened_at`/`closed_at` apareceu de novo ao
  ler `Trade.created_at` de volta do banco para calcular um cooldown (história 34). Normalize com
  `momento if momento.tzinfo is not None else momento.replace(tzinfo=UTC)` (ou chame o helper já
  existente) sempre que uma coluna `TimestampTZ` lida do banco for usada em aritmética de data em
  Python, não só em filtro `WHERE` (que não sofre o problema, porque a comparação roda no SQL).
- **Adicionar componente novo ao `technical_analyzer` muda a confiança da fusão de fixtures de
  teste já calibradas em outros módulos, mesmo sem tocar neles.** A história 35 acrescentou 4
  alpha factors (`momentum`, `mean_reversion`, `relative_volatility`, `trend_strength`) à mesma
  média que já tinha `rsi`/`macd`/`bollinger`. Numa rampa reta de preço, os fatores de reversão
  (rsi/bollinger/mean_reversion) sempre discordam dos de tendência (momentum/trend_strength) — é
  o comportamento correto (reversão prevê puxada, tendência prevê continuação), mas derruba a
  concordância e, por consequência, a confiança da fusão. Três testes em `test_runner.py`
  calibrados na história 24 (fixture de rampa reta com `min_signal_confidence` default) ficaram
  abaixo do limiar e pararam de gerar ordem; corrigido com override
  `_runner(min_signal_confidence=0.01)`, já que o próprio teste documenta que a direção/confiança
  exata não importa, só que `_process_symbol` chega até `_executar`. Mesmo padrão da história 33
  (mudar constante interna quebra teste que fixa valor numérico calibrado alhures) — ao adicionar
  componente novo à média do `technical_analyzer`, procure teste de outro módulo que dependa do
  valor concreto de confiança/score resultante, não só do sinal (BUY/SELL/HOLD).
- **Bloqueio do runner que nunca chega ao `order_manager` (confiança, cooldown, margem, ATR/stop
  inválidos, risco que não paga o lote mínimo) só existia como log estruturado — nada gravado no
  banco.** A história 36 acrescentou `AuditEventType.ORDER_BLOCKED` e um helper único
  (`BotRunner._registrar_bloqueio`) chamado em todo `return` cedo de `_process_symbol`/`_executar`
  que não passa pelo `risk_manager`. Kill switch e drawdown **não** duplicam esse evento — eles já
  persistem seu próprio motivo (`KILL_SWITCH_TRIGGERED`/`DRAWDOWN_LIMIT_TRIGGERED`, histórias
  18/29) e, como o ciclo retorna antes de processar qualquer símbolo enquanto o bloqueio está
  ativo, nenhum evento mais recente sobrescreve o motivo no audit log — o evento de trigger
  continua sendo o mais recente até o reset. O dashboard deriva "por que a última ordem foi
  bloqueada" filtrando o audit log (já carregado, sem endpoint novo) pelos tipos relevantes
  (`order_placed`, `order_blocked`, `order_rejected`, `kill_switch_*`, `drawdown_limit_*`) e
  pegando o primeiro (mais recente primeiro na query). Ver `frontend/src/lib/blockReason.ts`.
- **Dois gates de tamanho de ordem podem colapsar no mesmo log/motivo mesmo sendo causas
  diferentes.** `runner._executar` usa o mesmo evento `runner.margem_insuficiente` /
  `motivo="margem_insuficiente"` tanto para margem livre real insuficiente quanto para o caso em
  que `VOLUME_MAX_PER_ORDER_LOTS` (teto fixo) corta o volume abaixo do `min_volume` do broker —
  são causas diferentes, mesmo rótulo. Ao escrever teste para "risco não cobre o lote mínimo"
  (primeiro gate, `_calcular_volume` devolve `None`), garanta que `volume_min` fique ACIMA do
  volume bruto calculado pelo risco; um `volume_min` só um pouco maior deixa o volume bruto passar
  no primeiro gate e cair no segundo (teto por ordem + margem), gravando o motivo errado.
- **Dashboard como fiscal do pipeline: "posições abertas" batendo com o banco, não com o broker, é
  bug, não feature.** A história 36 ligou `GET /trades/` (posições ao vivo do MT5, endpoint já
  existia mas não era consumido pelo frontend) na Visão geral. Rodando localmente contra a conta
  demo real desta máquina, o banco tinha 5 trades `status=open`, mas o broker devolveu zero
  posições — divergência real, não hipotética (reconciliação do `position_tracker` está atrasada
  ou os trades já fecharam fora do fluxo rastreado). Isso confirma por que a história pediu "bate
  com o broker, não só com o banco": mostrar o status do banco como se fosse a posição atual
  teria sido literalmente falso nesse momento.
- **Gráfico "derivado por suposição" ≠ gráfico "sem dado real".** A curva de equity (história 22)
  foi removida nesta história porque era reconstruída de trás para frente a partir do equity atual
  menos o PnL fechado (nunca existiu tabela de histórico) — correta só enquanto nenhuma posição
  fica aberta entre o início da série e o F5, premissa que quebra sozinha com o bot rodando. Os
  outros 4 gráficos (distribuição de P&L, win rate por par, sentimento vs. técnica, heatmap por
  hora) ficaram: são agregações honestas de dado já persistido, não extrapolação — a AC "remover
  gráfico sem dado real por trás" mirava um caso específico, não um convite a esvaziar a Visão
  geral.
- **Registro de estratégias paralelas (história 39): `backend/analysis/strategy.py`.** Protocol
  `Strategy` (`evaluate(candles) -> StrategySignal | None`, `None` só para ausência de setup, nunca
  para HOLD) + `STRATEGY_REGISTRY: dict[str, Callable[[], Strategy]]` + `build_enabled_strategies`
  que falha (`ValueError`) na construção do `BotRunner` para nome desconhecido em
  `STRATEGIES_ENABLED` — nunca no meio de um ciclo. `TechnicalStrategy` (`name="technical"`) é a
  leitura que já existia, preservada como legado/default. `BotRunner._process_symbol` lê
  ATR/sentimento **uma vez por símbolo** (dado de mercado, compartilhado) e itera todas as
  estratégias habilitadas; cada uma vira um `Signal` próprio via `fuse_signals` e, se aprovada,
  uma ordem própria. Uma estratégia nova (histórias 40-42) só precisa implementar o Protocol e
  entrar no `STRATEGY_REGISTRY` — não toca no runner.
- **`signals.strategy` e `trades.strategy` são colunas distintas de propósito, não duplicação por
  descuido.** `Signal.strategy` é a fonte de verdade para performance por estratégia (`GET
  /strategies/performance`, junta `Outcome` a `Signal`). `Trade.strategy` é denormalizado para o
  cooldown por (símbolo, direção, estratégia) em `_pode_abrir_posicao` funcionar mesmo quando
  `Trade.signal_id` é nulo (trade manual, ou teste que chama `_executar` direto) — um JOIN a
  `Signal` faria esses trades "sumirem" da checagem de cooldown. `client_request_id` também ganhou
  a estratégia no meio (`bot-{symbol}-{side}-{strategy}-{bucket}`) pelo mesmo motivo: idempotência
  e cooldown não podem colidir entre estratégias diferentes no mesmo símbolo/direção.
- **Fator alpha novo em `IndicatorSnapshot`: raw no snapshot, normalização no `_score_*`.** Mesmo
  padrão do MACD/ATR: o snapshot guarda o valor cru (`momentum_5`, `reversion_mean`,
  `atr_baseline`, `adx_pos`...) calculado via `close.diff()`/`.rolling()`/`ADXIndicator` da lib
  `ta`; a função `_score_*` faz a divisão que dá a escala (por ATR, por desvio-padrão, por soma de
  DI) e devolve 0.0 quando o denominador é ≤0 — nunca um valor forjado. Como o campo cru entra na
  mesma lista `valores` que os indicadores antigos dentro de `compute_indicators`, ele herda de
  graça a checagem de NaN (série curta ou em aquecimento já devolve `None` sem código extra),
  desde que a janela do fator novo seja menor que `minimum_candles()` (35, imposto pelo MACD) —
  janelas de até ~20-33 candles não exigem mexer nessa constante.
- **Estratégia de padrão gráfico (BBRSI, história 40) é sinal binário, não score contínuo — e o
  registro é a fonte externa (fonte MQL5 do EA), não invenção.** Diferente de `TechnicalStrategy`
  (média de componentes contínuos), `BBRSIStrategy.evaluate` verifica as seis condições exatas do
  EA original (`RSI[2]`/`Close[2]` = `iloc[-2]`, `RSI[1]`/`Close[1]` = `iloc[-1]` — o índice 0 da
  fonte MQL5 é a barra ainda em formação, que não existe no nosso DataFrame de candles fechados) e
  devolve `score=±1.0, confidence=1.0` quando batem, ou `HOLD` com `confidence=0.0` quando não —
  não há posição intermediária, porque a fonte não tem. Ao portar uma estratégia de outro
  repositório, buscar o `.mq5`/código-fonte real (`WebFetch` no raw do GitHub) em vez de assumir a
  partir da descrição do PRD é o que evita reinterpretar errado uma condição — a descrição do PRD
  aqui já vinha extraída literalmente da fonte, mas o coeficiente do SL (`SLCoef=0.9`) e a
  convenção de índice de barra só apareceram lendo o `.mq5`.
- **`StrategySignal` ganhou `stop_loss: float | None = None` para uma estratégia definir o próprio
  stop, e o runner passou a aceitar `stop_loss_override` em `_executar`.** Até a história 40, todo
  stop vinha do ATR global (`atr * atr_sl_multiplier`); BBRSI deriva o stop da própria banda
  (`BB_L - coef*(BB_M-BB_L)`), incompatível com essa fórmula. Quando o override está presente, o
  guard de `atr<=0` (que bloqueava ordem sem volatilidade medida) é pulado — ele só faz sentido
  quando o stop *depende* do ATR — e `distancia_sl` vira `abs(entry - stop_loss)` para o resto do
  pipeline (volume por risco, take profit por RR) continuar funcionando sem duplicar lógica. Esse é
  o padrão a seguir para 3MACD/2MACDSTO (histórias 41-42) caso alguma delas também defina stop
  próprio — checar a fonte antes de assumir que é o caso; nem toda estratégia da fonte tem SL
  derivado do próprio indicador.
- **`STRATEGY_REGISTRY` cresceu, e isso quebra qualquer teste que usava um nome de estratégia
  "desconhecida" igual ao nome real de uma história ainda não implementada.** Dois testes
  (`test_strategy.py::test_build_enabled_strategies_nome_desconhecido_falha` e
  `test_runner.py::test_botrunner_falha_no_boot_com_estrategia_desconhecida`) usavam `"bbrsi"`
  como exemplo de nome inválido — válido só até esta história registrar `"bbrsi"` de verdade. Ao
  registrar uma estratégia nova, `grep` pelo nome dela em todo `backend/tests/` antes de rodar a
  suíte: um teste "nome desconhecido" que usa por acaso o nome que está prestes a virar válido
  passa a falhar silenciosamente ao ficar sem cobrir o caminho de erro que deveria testar.
- **Candle mínimo de uma estratégia (`bb_len + 1` no BBRSI) não é o mesmo mínimo do
  `technical_analyzer` (`minimum_candles()`, 35) nem do `CANDLES_POR_CICLO` do runner (120).** Com
  os defaults reais da fonte (`BB_LEN=500`), a estratégia só produz sinal com pelo menos 501
  candles — mais que o runner busca hoje. Isso é esperado e não bloqueia a história (a AC pede
  justamente que série curta devolva `None`, não sinal forjado), mas registrar aqui: habilitar
  `"bbrsi"` em `STRATEGIES_ENABLED` sem também aumentar `CANDLES_POR_CICLO` deixa a estratégia
  sempre em `None` — decisão de configuração do operador, não bug, mas fácil de não perceber sem
  este registro.

---

## Onde estão as coisas

| O quê | Onde |
|---|---|
| Regras de risco (valores) | `backend/config.py` |
| Invariantes do projeto | `CLAUDE.md` |
| Histórias e critérios | `prd.json` / `tasks/prd-forex-bot.md` |
| Infra de estado | `docker-compose.yml` (só Postgres + Redis) |
| CI | `.github/workflows/ci.yml` |
| Endpoints do dashboard | `backend/api/routers/{trades,signals,audit,promotion,strategies}.py` |
| Registro de estratégias | `backend/analysis/strategy.py` |
| Componentes do dashboard | `frontend/src/components/{charts,modals,tabs}/` |
| Fetchers + tipos do frontend | `frontend/src/lib/api.ts` |

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
- **Ruff B008 e FastAPI Depends:** O `ruff` acusa `B008` ao chamar funções como `Depends(get_db)` direto na assinatura da função. Para calar o alerta de forma cirúrgica num endpoint (como o `/ready`), adicione `# noqa: B008` na linha.
- **Modelos sem `relationship()` é deliberado.** `Trade`, `Signal`, `Outcome`, `Instrument` só têm FK (`instrument_id`, `signal_id`, `trade_id`), nunca `relationship()`. Endpoint que precisa juntar tabelas (ex: `/trades/history`) faz `db.query(Trade, Instrument.symbol, Signal, Outcome).join(...).outerjoin(...)` explícito em vez de navegar atributo. Ao adicionar endpoint novo que cruza tabelas, siga o mesmo padrão — não adicione `relationship()` só para um endpoint.
- **React Compiler linta `useEffect` com rigor (Next 16 / React 19).** `eslint-plugin-react-hooks` acusa `react-hooks/set-state-in-effect` para qualquer `setState` (direto ou via função assíncrona) chamado dentro do corpo de um `useEffect`, e `react-hooks/purity` para `Date.now()`/`Math.random()`/`new Date()` sem argumento chamados durante o render (inclusive dentro de `useMemo`). Ambos são falsos positivos legítimos para fetch inicial de dados e filtro por janela de tempo — resolvido com `// eslint-disable-next-line <regra>` pontual, comentando o motivo. `new Date(isoString)` (com argumento) não é pego pela regra de pureza.
- **Tailwind v4 dark mode por classe, não por media query.** `@custom-variant dark (&:where(.dark, .dark *));` no topo do `globals.css` + toggle de `.dark` no `<html>` via JS. Tokens de cor ficam em `@theme inline` apontando para variáveis simples (`--background`, `--card`, etc.), redefinidas dentro de `.dark { }` — é o padrão que o shadcn/ui usa para Tailwind v4, permite `bg-card`, `text-fg-secondary` etc. funcionarem nos dois temas sem duplicar classe em todo componente. Script inline em `layout.tsx` aplica a classe antes do primeiro paint (evita flash); `suppressHydrationWarning` no `<html>`/`<body>` é obrigatório porque esse script roda fora do ciclo do React.
- **Paleta de gráfico dedicada, não a paleta de marca.** Azul/roxo da marca (`--color-primary`/`--color-accent`) falha o validador da skill `dataviz` como par categórico (ΔE 1.3 deutan, 12.0 visão normal — abaixo do piso de 15). Séries de gráfico usam `frontend/src/lib/palette.ts`, validado com `node scripts/validate_palette.js` da skill contra as duas superfícies (clara/escura) deste app: azul/laranja para categórico, verde/vermelho (`good`/`critical`) reservados para estado (lucro/prejuízo), nunca reaproveitados como cor de série.
- **Sem tabela de equity histórico.** O MT5 só devolve o equity atual, não série temporal. A curva de capital do dashboard é reconstruída de trás para frente: parte do equity atual e desconta o `pnl` de cada trade encerrado andando no tempo (`buildEquityCurve` em `EquityCurveChart.tsx`). É dado real, não interpolado — mas reinicia do zero a cada full reload porque não persiste estado entre sessões do navegador.
- **Backend + frontend rodando localmente durante dev podem colidir com processo velho.** `netstat -ano | grep :PORTA` + `Get-CimInstance Win32_Process -Filter "ProcessId=X"` (PowerShell) identifica se a porta já está ocupada por um `uvicorn`/`next dev` de uma sessão anterior rodando código desatualizado (sem `--reload` no caso do uvicorn). `next dev` faz Fast Refresh sozinho ao detectar mudança de arquivo; `uvicorn` sem `--reload` não — precisa matar e subir de novo para refletir edição.
- **Verificação visual sem `chromium-cli` disponível:** `npx --yes -p playwright playwright install chromium` baixa o browser; o script de verificação não pode rodar com `node script.js` direto (o módulo `playwright` não resolve fora de um projeto que o declara) — descubra o cache do npx (`npm config get cache`, pasta `_npx/<hash>/node_modules`) e exporte `NODE_PATH` apontando pra lá antes de rodar `node script.js`.
