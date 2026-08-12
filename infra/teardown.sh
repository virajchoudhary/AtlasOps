#!/usr/bin/env bash
# infra/teardown.sh — guarded AtlasOps development-resource cleanup
set -euo pipefail

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "${SCRIPT_DIR}/teardown_impl.sh" "$@"
