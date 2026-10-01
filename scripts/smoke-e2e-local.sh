#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-verbose}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "$MODE" == "quiet" ]]; then
  exec python "${SCRIPT_DIR}/smoke_e2e_local.py" --quiet
else
  exec python "${SCRIPT_DIR}/smoke_e2e_local.py"
fi
