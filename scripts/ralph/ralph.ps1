<#
.SYNOPSIS
    Ralph loop para Windows — uma história por iteração, instância fresca do Claude.

.DESCRIPTION
    Equivalente ao ralph.sh. Existe porque a máquina de desenvolvimento é Windows e
    depender de Git Bash só para rodar o loop é atrito desnecessário.

    Memória entre iterações: git history + progress.txt + prd.json + AGENTS.md.
    Para quando o agente emite <promise>COMPLETE</promise>.

.PARAMETER MaxIterations
    Número máximo de iterações. Default: 10.

.EXAMPLE
    .\scripts\ralph\ralph.ps1 30
#>
[CmdletBinding()]
param(
    [int]$MaxIterations = 10
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $Root

$PromptFile = Join-Path $Root "scripts\ralph\prompt.md"
$Prd        = Join-Path $Root "prd.json"
$Progress   = Join-Path $Root "progress.txt"

foreach ($cmd in @("git", "uv", "claude", "npx")) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        throw "'$cmd' nao encontrado no PATH."
    }
}
if (-not (Test-Path $Prd))        { throw "prd.json nao existe." }
if (-not (Test-Path $PromptFile)) { throw "scripts\ralph\prompt.md nao existe." }
if (-not (Test-Path "$Root\.claude-flow")) { Write-Host "Aviso: Ruflo nao inicializado! Recomenda-se rodar 'npx ruflo init'." }

function Get-HistoriasAbertas {
    $prd = Get-Content $Prd -Raw | ConvertFrom-Json
    @($prd.userStories | Where-Object { -not $_.passes })
}

if (-not (Test-Path $Progress)) {
    Set-Content -Path $Progress -Value "## Codebase Patterns`n`n---" -Encoding utf8
}

Write-Host "Ralph: ate $MaxIterations iteracoes. Raiz: $Root"

for ($i = 1; $i -le $MaxIterations; $i++) {
    $abertas = Get-HistoriasAbertas
    if ($abertas.Count -eq 0) {
        Write-Host "<promise>COMPLETE</promise>"
        exit 0
    }

    $proxima = $abertas | Sort-Object id | Select-Object -First 1
    Write-Host ""
    Write-Host "==============================================================="
    Write-Host "  Ralph iteracao $i/$MaxIterations - proxima: #$($proxima.id) $($proxima.title)"
    Write-Host "  ($($abertas.Count) historia(s) restante(s))"
    Write-Host "==============================================================="

    # ReadAllText com UTF8 explicito: `Get-Content -Raw` no PowerShell 5.1 le
    # arquivo UTF-8 sem BOM como ANSI e entrega o prompt com acentos corrompidos.
    $promptText = [System.IO.File]::ReadAllText($PromptFile, [System.Text.Encoding]::UTF8)
    $output = $promptText | & claude --dangerously-skip-permissions --print
    $output | Write-Host

    if ($output -match "<promise>COMPLETE</promise>") {
        Write-Host ""
        Write-Host "Ralph concluiu todas as historias na iteracao $i."
        exit 0
    }

    # Guarda contra loop improdutivo: se nada foi concluido, para para inspecao.
    if ((Get-HistoriasAbertas).Count -eq $abertas.Count) {
        $msg = "Nenhuma historia concluida na iteracao $i. Parando para inspecao."
        Write-Host $msg
        Add-Content -Path $Progress -Value $msg -Encoding utf8
        exit 1
    }

    Start-Sleep -Seconds 2
}

Write-Host ""
Write-Host "Limite de $MaxIterations iteracoes atingido. Historias restantes:"
Get-HistoriasAbertas | Sort-Object id | ForEach-Object { Write-Host "  #$($_.id) $($_.title)" }
exit 1
