<#
.SYNOPSIS
    Sobe o forex-bot inteiro do zero: Docker, migrations, API, frontend,
    Celery worker+beat e o bot autônomo — cada um em sua própria janela.

.DESCRIPTION
    Rotina de boot único. Pré-requisitos que o script NÃO cobre (o operador
    faz manualmente, na ordem):
      1. Ligar o PC
      2. Abrir o Docker Desktop e esperar ele ficar pronto
      3. Abrir o terminal MetaTrader 5 e logar na conta demo
    Depois disso, um único comando:

        .\scripts\start_all.ps1

    Cada serviço abre em janela PowerShell própria (Start-Process sem -Hidden)
    porque `Start-Process cmd.exe /c "..."` morre junto com a sessão que o
    lançou — gotcha documentado em HANDOFF.md. Janela própria sobrevive ao
    script terminar.

.PARAMETER SkipBot
    Sobe toda a infraestrutura (Docker, API, frontend, Celery) mas não inicia
    o BotRunner — útil para inspecionar o dashboard sem operar.
#>
[CmdletBinding()]
param(
    [switch]$SkipBot
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Write-Step {
    param([string]$Msg)
    Write-Host ""
    Write-Host "==> $Msg" -ForegroundColor Cyan
}

function Write-Ok {
    param([string]$Msg)
    Write-Host "    OK: $Msg" -ForegroundColor Green
}

function Write-Fail {
    param([string]$Msg)
    Write-Host "    FALHA: $Msg" -ForegroundColor Red
}

# -- 1. Pré-requisitos ---------------------------------------------------

Write-Step "Verificando pré-requisitos"

foreach ($cmd in @("docker", "uv", "npm")) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        Write-Fail "'$cmd' nao encontrado no PATH. Instale antes de continuar."
        exit 1
    }
}
Write-Ok "docker, uv, npm no PATH"

try {
    docker info *> $null
    if ($LASTEXITCODE -ne 0) { throw "docker info falhou" }
} catch {
    Write-Fail "Docker Desktop nao esta rodando. Abra o Docker Desktop e espere ele ficar pronto, depois rode este script de novo."
    exit 1
}
Write-Ok "Docker Desktop respondendo"

if (-not (Test-Path "$Root\.env")) {
    Write-Fail ".env nao existe em $Root. Copie .env.example para .env e preencha as credenciais MT5."
    exit 1
}
Write-Ok ".env encontrado"

# MT5 roda como processo nativo no Windows, nao tem healthcheck de CLI —
# só um aviso, o proprio bot falha fechado se o terminal nao estiver logado.
$mt5Proc = Get-Process -Name "terminal64" -ErrorAction SilentlyContinue
if (-not $mt5Proc) {
    Write-Host "    AVISO: nao detectei o processo do MetaTrader 5 (terminal64.exe)." -ForegroundColor Yellow
    Write-Host "    Abra o MT5 e logue na conta demo antes do bot tentar conectar." -ForegroundColor Yellow
} else {
    Write-Ok "MetaTrader 5 rodando (PID $($mt5Proc.Id))"
}

# -- 2. Docker Compose (Postgres + Redis) --------------------------------

Write-Step "Subindo Postgres e Redis (docker compose up -d)"
docker compose up -d
if ($LASTEXITCODE -ne 0) {
    Write-Fail "docker compose up falhou."
    exit 1
}

# Espera o Postgres aceitar conexao antes de migrar — subir o container nao
# significa que o Postgres ja aceita conexoes.
Write-Host "    Aguardando Postgres aceitar conexoes..."
$maxTentativas = 30
$ok = $false
for ($i = 1; $i -le $maxTentativas; $i++) {
    docker compose exec -T postgres pg_isready -U forex *> $null
    if ($LASTEXITCODE -eq 0) { $ok = $true; break }
    Start-Sleep -Seconds 1
}
if (-not $ok) {
    Write-Fail "Postgres nao respondeu apos $maxTentativas segundos."
    exit 1
}
Write-Ok "Postgres pronto"

# -- 3. Migrations --------------------------------------------------------

Write-Step "Aplicando migrations (alembic upgrade head)"
uv run alembic upgrade head
if ($LASTEXITCODE -ne 0) {
    Write-Fail "alembic upgrade falhou."
    exit 1
}
Write-Ok "Schema atualizado"

# -- 4. Serviços em janelas próprias --------------------------------------
#
# Start-Process sem -WindowStyle Hidden: cada serviço fica visível e
# sobrevive ao script terminar. `cmd.exe /c` como shell intermediário morre
# junto com a sessão que o lançou (gotcha do HANDOFF.md) — aqui lançamos
# powershell.exe diretamente com -NoExit para manter a janela viva.

function Start-Servico {
    param(
        [string]$Titulo,
        [string]$Comando
    )
    Write-Host "    Abrindo janela: $Titulo"
    Start-Process powershell.exe -ArgumentList @(
        "-NoExit",
        "-Command",
        "`$Host.UI.RawUI.WindowTitle = '$Titulo'; Set-Location '$Root'; $Comando"
    )
}

Write-Step "Subindo API FastAPI (porta 8001)"
Start-Servico -Titulo "forex-bot: API" `
    -Comando "uv run uvicorn backend.api.main:app --host 127.0.0.1 --port 8001"

Write-Step "Subindo frontend Next.js (porta 3000)"
Start-Servico -Titulo "forex-bot: Frontend" `
    -Comando "Set-Location '$Root\frontend'; npm run dev"

Write-Step "Subindo Celery worker"
Start-Servico -Titulo "forex-bot: Celery Worker" `
    -Comando "uv run celery -A backend.celery_app worker -l info -P solo"

Write-Step "Subindo Celery beat"
Start-Servico -Titulo "forex-bot: Celery Beat" `
    -Comando "uv run celery -A backend.celery_app beat -l info"

# Celery worker precisa estar de pé antes do bot gerar trades que dependem
# de position_tracker para reconciliar — pequena folga evita corrida.
Write-Host "    Aguardando Celery inicializar..."
Start-Sleep -Seconds 5

if (-not $SkipBot) {
    Write-Step "Subindo o bot autônomo (BotRunner)"
    Start-Servico -Titulo "forex-bot: BOT" `
        -Comando "uv run python scripts/run_bot.py"
} else {
    Write-Step "SkipBot ativo: bot NAO iniciado (infra de pé, ligue manualmente quando quiser)"
}

# -- 5. Resumo -------------------------------------------------------------

Write-Step "Tudo de pé"
Write-Host ""
Write-Host "  API:        http://127.0.0.1:8001" -ForegroundColor White
Write-Host "  Dashboard:  http://localhost:3000" -ForegroundColor White
Write-Host "  Auditoria:  uv run python scripts/auditar_db.py --follow" -ForegroundColor White
Write-Host ""
Write-Host "  5 janelas abertas: API, Frontend, Celery Worker, Celery Beat" -NoNewline
if (-not $SkipBot) { Write-Host ", Bot" } else { Write-Host "" }
Write-Host "  Feche as janelas individualmente para parar cada serviço." -ForegroundColor DarkGray
