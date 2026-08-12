#!/usr/bin/env bash
# infra/setup.sh — guarded AtlasOps development-infrastructure provisioning
set -euo pipefail

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "${SCRIPT_DIR}/setup_impl.sh" "$@"
