#!/usr/bin/env bash
# Ralph loop — uma história por iteração, cada uma em instância fresca do Claude.
# Adaptado de https://github.com/snarktank/ralph para o stack Python deste repo.
#
# Uso:  ./scripts/ralph/ralph.sh [MAX_ITER]      (default: 10)
#
# Memória entre iterações: git history + progress.txt + prd.json + AGENTS.md.
# Para quando o agente emite <promise>COMPLETE</promise>.
#
# Requer: git, uv e a CLI `claude` no PATH. Sem jq: o prd.json é lido por Python,
# que já é dependência do projeto.

set -euo pipefail

MAX_ITER="${1:-10}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

PROMPT_FILE="scripts/ralph/prompt.md"
PRD="prd.json"
PROGRESS="progress.txt"

for cmd in git uv claude; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "ERRO: '$cmd' não encontrado no PATH."; exit 1; }
done
[ -f "$PRD" ] || { echo "ERRO: $PRD não existe."; exit 1; }
[ -f "$PROMPT_FILE" ] || { echo "ERRO: $PROMPT_FILE não existe."; exit 1; }

pending() {
  uv run python -c "
import json, sys
d = json.load(open('$PRD', encoding='utf-8'))
abertas = [s for s in d['userStories'] if not s['passes']]
print(len(abertas))
if abertas:
    s = min(abertas, key=lambda x: x['id'])
    print(f\"{s['id']}|{s['title']}\", file=sys.stderr)
"
}

[ -f "$PROGRESS" ] || printf '## Codebase Patterns\n\n---\n' > "$PROGRESS"

echo "Ralph: até $MAX_ITER iterações. Raiz: $ROOT"

for ((i = 1; i <= MAX_ITER; i++)); do
  RESTANTES="$(pending 2>/dev/null)"
  if [ "$RESTANTES" -eq 0 ]; then
    echo "<promise>COMPLETE</promise>"
    exit 0
  fi

  echo ""
  echo "==============================================================="
  echo "  Ralph iteração $i/$MAX_ITER — $RESTANTES história(s) restante(s)"
  echo "==============================================================="

  OUTPUT="$(claude --dangerously-skip-permissions --print < "$PROMPT_FILE" 2>&1 | tee /dev/stderr)" || true

  if grep -q "<promise>COMPLETE</promise>" <<<"$OUTPUT"; then
    echo ""
    echo "Ralph concluiu todas as histórias na iteração $i."
    exit 0
  fi

  # Guarda contra loop improdutivo: se nada foi marcado como concluído, para.
  DEPOIS="$(pending 2>/dev/null)"
  if [ "$DEPOIS" -eq "$RESTANTES" ]; then
    echo "Nenhuma história foi concluída na iteração $i. Parando para inspeção." | tee -a "$PROGRESS"
    exit 1
  fi

  sleep 2
done

echo ""
echo "Limite de $MAX_ITER iterações atingido. Histórias restantes:"
uv run python -c "
import json
d = json.load(open('$PRD', encoding='utf-8'))
for s in d['userStories']:
    if not s['passes']:
        print(f\"  #{s['id']} {s['title']}\")
"
exit 1
