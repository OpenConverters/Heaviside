#!/usr/bin/env bash
# Run LLM-gated tests (CR, BOM extraction, Kimi smoke) with .env loaded.
# Usage: ./scripts/run_llm_tests.sh [extra pytest args]
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$REPO/.env"

if [[ ! -f "$ENV_FILE" ]]; then
    echo "ERROR: $ENV_FILE not found" >&2
    exit 1
fi

# Export all non-comment vars from .env — works in background and subshells
export $(grep -v '^\s*#' "$ENV_FILE" | grep -v '^\s*$' | xargs)

export HEAVISIDE_RUN_LLM_CR=1

exec "$REPO/.venv-web/bin/python" -m pytest \
    tests/regression/test_cr_vs_proteus_llm.py \
    tests/regression/test_bom_extraction_vs_proteus.py \
    tests/evals/test_kimi_smoke.py \
    -v "$@"
