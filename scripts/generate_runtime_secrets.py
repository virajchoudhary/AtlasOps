#!/usr/bin/env python3
"""AtlasOps Local Runtime Secret Preparation Helper.

Generates local gitignored secret material required for AtlasOps coordinator,
Alertmanager, and Argo CD before running infra/setup.sh --apply.

Primary responsibility: LOCAL MATERIAL PREPARATION.
The canonical infra/setup.sh --apply directly provisions Kubernetes Secrets
from these local files into the isolated cluster context.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import secrets
import stat
import string


def generate_token(length: int = 32) -> str:
    """Generate a cryptographically secure random hexadecimal token."""
    return secrets.token_hex(length)


def generate_secure_password(length: int = 24) -> str:
    """Generate a strong random password for Argo CD local account."""
    alphabet = string.ascii_letters + string.digits + "-_.~"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def set_restricted_permissions(file_path: Path) -> None:
    """Set file permissions to 0600 (owner read/write only) on POSIX systems.

    On Windows, the secrets/ directory is .gitignored and protected by standard
    NTFS user-profile ACL inheritance.
    """
    if os.name == "posix":
        file_path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prepare local AtlasOps runtime secret files for infra/setup.sh"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("secrets"),
        help="Local directory to write secret files to (default: secrets/)",
    )
    parser.add_argument(
        "--argocd-user",
        type=str,
        default="atlasops",
        help="Argo CD dedicated least-privilege username (default: atlasops)",
    )
    parser.add_argument(
        "--argocd-pass-file",
        type=Path,
        default=None,
        help="Optional path to existing file containing the Argo CD password (generated if omitted)",
    )
    parser.add_argument(
        "--llm-api-key-file",
        type=Path,
        default=None,
        help="Optional path to existing file containing the LLM API key (for non-vLLM backends)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing secret files in output directory",
    )
    args = parser.parse_args()

    out_dir: Path = args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Generic Coordinator & Alertmanager Secrets
    audit_file = out_dir / "atlasops-audit-secret.secret"
    webhook_file = out_dir / "alertmanager-webhook-secret.secret"
    api_key_file = out_dir / "atlasops-api-key.secret"
    user_file = out_dir / "argocd-user.secret"
    pass_file = out_dir / "argocd-pass.secret"
    llm_file = out_dir / "llm-api-key.secret"

    if not audit_file.is_file() or args.force:
        audit_file.write_text(generate_token(32), encoding="utf-8")
        set_restricted_permissions(audit_file)

    if not webhook_file.is_file() or args.force:
        webhook_file.write_text(generate_token(32), encoding="utf-8")
        set_restricted_permissions(webhook_file)

    if not api_key_file.is_file() or args.force:
        api_key_file.write_text(generate_token(32), encoding="utf-8")
        set_restricted_permissions(api_key_file)

    # 2. Argo CD Local Account Credentials
    user_file.write_text(args.argocd_user.strip(), encoding="utf-8")
    set_restricted_permissions(user_file)

    if args.argocd_pass_file:
        if not args.argocd_pass_file.is_file():
            raise FileNotFoundError(f"Specified Argo password file not found: {args.argocd_pass_file}")
        pass_content = args.argocd_pass_file.read_text(encoding="utf-8").strip()
        pass_file.write_text(pass_content, encoding="utf-8")
        set_restricted_permissions(pass_file)
    elif not pass_file.is_file() or args.force:
        generated_pwd = generate_secure_password(24)
        pass_file.write_text(generated_pwd, encoding="utf-8")
        set_restricted_permissions(pass_file)

    # 3. Optional LLM API Key
    if args.llm_api_key_file:
        if not args.llm_api_key_file.is_file():
            raise FileNotFoundError(f"Specified LLM API key file not found: {args.llm_api_key_file}")
        llm_content = args.llm_api_key_file.read_text(encoding="utf-8").strip()
        llm_file.write_text(llm_content, encoding="utf-8")
        set_restricted_permissions(llm_file)

    # 4. Generate Manual/Emergency Recovery Script (Requires Explicit --context)
    recovery_script_content = """#!/usr/bin/env bash
# ==============================================================================
# AtlasOps Runtime Secrets - Manual / Emergency Recovery Apply Script
# NOTE: The canonical infra/setup.sh --apply automatically provisions Kubernetes Secrets
# directly from local secret files. This script is for manual recovery only.
#
# USAGE: bash secrets/apply_secrets.sh --context <KUBE_CONTEXT>
# DO NOT RUN WITHOUT EXPLICIT CONTEXT. Ambient contexts are rejected.
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_CONTEXT=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --context)
      TARGET_CONTEXT="$2"
      shift 2
      ;;
    *)
      echo "ERROR: Unknown argument: $1" >&2
      echo "Usage: bash $0 --context <KUBE_CONTEXT>" >&2
      exit 1
      ;;
  esac
done

if [[ -z "$TARGET_CONTEXT" ]]; then
  echo "ERROR: Explicit --context <KUBE_CONTEXT> is required for recovery secret application." >&2
  echo "Ambient kubectl contexts are intentionally rejected to prevent accidental mutation." >&2
  exit 1
fi

echo "RECOVERY: Applying secrets to target context '$TARGET_CONTEXT' from '$SCRIPT_DIR'..."

for req in atlasops-audit-secret.secret alertmanager-webhook-secret.secret atlasops-api-key.secret argocd-user.secret argocd-pass.secret; do
  if [[ ! -s "$SCRIPT_DIR/$req" ]]; then
    echo "ERROR: Missing or empty required secret file: $SCRIPT_DIR/$req" >&2
    exit 1
  fi
done

kubectl --context="$TARGET_CONTEXT" create namespace default --dry-run=client -o yaml | kubectl --context="$TARGET_CONTEXT" apply -f -
kubectl --context="$TARGET_CONTEXT" create namespace monitoring --dry-run=client -o yaml | kubectl --context="$TARGET_CONTEXT" apply -f -

kubectl --context="$TARGET_CONTEXT" create secret generic atlasops-coordinator-secrets \\
  --namespace=default \\
  --from-file=atlasops-audit-secret="$SCRIPT_DIR/atlasops-audit-secret.secret" \\
  --from-file=alertmanager-webhook-secret="$SCRIPT_DIR/alertmanager-webhook-secret.secret" \\
  --from-file=atlasops-api-key="$SCRIPT_DIR/atlasops-api-key.secret" \\
  --from-file=argocd-user="$SCRIPT_DIR/argocd-user.secret" \\
  --from-file=argocd-pass="$SCRIPT_DIR/argocd-pass.secret" \\
  --dry-run=client -o yaml | kubectl --context="$TARGET_CONTEXT" apply -f -

kubectl --context="$TARGET_CONTEXT" create secret generic atlasops-alertmanager-webhook \\
  --namespace=monitoring \\
  --from-file=alertmanager-webhook-secret="$SCRIPT_DIR/alertmanager-webhook-secret.secret" \\
  --dry-run=client -o yaml | kubectl --context="$TARGET_CONTEXT" apply -f -

echo "RECOVERY: Successfully applied secrets to context '$TARGET_CONTEXT'."
"""

    recovery_script = out_dir / "apply_secrets.sh"
    recovery_script.write_text(recovery_script_content, encoding="utf-8")
    if os.name == "posix":
        recovery_script.chmod(recovery_script.stat().st_mode | stat.S_IXUSR)

    print(f"[OK] Prepared local runtime secret material in '{out_dir}/'")
    print(f"[OK] - Coordinator secrets: audit, webhook, api-key, argocd-user ({args.argocd_user}), argocd-pass")
    print("[OK] - Alertmanager secret: webhook token")
    if (out_dir / "llm-api-key.secret").is_file():
        print("[OK] - LLM API key provisioned")
    print("[OK] Local secret material is complete. Ready for infra/setup.sh --apply.")


if __name__ == "__main__":
    main()
