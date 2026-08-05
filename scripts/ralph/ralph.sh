#!/usr/bin/env bash
# Ralph loop — uma história por iteração, cada uma em instância fresca de IA.
#
# Uso:  ./scripts/ralph/ralph.sh [MAX_ITER]     (default: 10)
#
# Memória entre iterações: git history + progress.txt + prd.json + AGENTS.md.
# Para quando todas as histórias estão com "passes": true.
#
# Requer: jq, git e a CLI `claude` no PATH.
# No Windows, rode pelo Git Bash.

set -euo pipefail

MAX_ITER="${1:-10}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

PRD="prd.json"
PROGRESS="progress.txt"

for cmd in jq git claude; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "ERRO: '$cmd' não encontrado no PATH."; exit 1; }
done
[ -f "$PRD" ] || { echo "ERRO: $PRD não existe. Gere o PRD antes."; exit 1; }

echo "Ralph: até $MAX_ITER iterações. Raiz: $ROOT"

for ((i = 1; i <= MAX_ITER; i++)); do
  STORY="$(jq -c 'first(.userStories[] | select(.passes == false))' "$PRD")"

  if [ -z "$STORY" ] || [ "$STORY" = "null" ]; then
    echo "<promise>COMPLETE</promise>"
    exit 0
  fi

  ID="$(jq -r '.id' <<<"$STORY")"
  TITLE="$(jq -r '.title' <<<"$STORY")"
  echo ""
  echo "=== Iteração $i/$MAX_ITER — história #$ID: $TITLE ==="

  PROMPT=$(cat <<EOF
Você está numa iteração do Ralph loop no repositório forex-bot.

Leia primeiro, na íntegra: CLAUDE.md, AGENTS.md, progress.txt e tasks/prd-forex-bot.md.
Consulte o git log para o que já foi construído.

Implemente EXATAMENTE esta história, nada além dela:

$(jq '.' <<<"$STORY")

Definition of Done (obrigatório, sem exceção):
  1. uv run ruff check . && uv run ruff format --check .
  2. uv run mypy backend
  3. uv run pytest --cov=backend --cov-report=term-missing
  4. commit granular e descritivo
  5. append de um aprendizado em progress.txt
  6. atualizar AGENTS.md com padrões/gotchas descobertos
  7. marcar "passes": true para a história $ID em prd.json e commitar

Se algum passo de verificação falhar, corrija antes de commitar. Não marque passes:true
com verificação vermelha. Não invente requisito fora do PRD. Siga Ponytail (YAGNI).
EOF
)

  if ! claude -p "$PROMPT" --permission-mode acceptEdits; then
    echo "Iteração $i falhou (história #$ID). Interrompendo o loop." | tee -a "$PROGRESS"
    exit 1
  fi

  # Guarda contra loop infinito: se a história não foi marcada, para.
  STILL_OPEN="$(jq -r --argjson id "$ID" \
    '.userStories[] | select(.id == $id) | .passes' "$PRD")"
  if [ "$STILL_OPEN" != "true" ]; then
    echo "História #$ID continua com passes:false após a iteração. Parando para inspeção."
    exit 1
  fi
done

echo ""
echo "Limite de $MAX_ITER iterações atingido. Histórias restantes:"
jq -r '.userStories[] | select(.passes == false) | "  #\(.id) \(.title)"' "$PRD"
