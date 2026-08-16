"""Executable simulated lifecycle and state-machine tests for Stage 3 bootstrap.

Executes infra/setup_impl.sh with mocked CLI boundaries (gcloud, kubectl, helm)
to verify the real end-to-end execution path, call ordering, state transitions,
local secret preflight, in-setup secret application, and Argo bcrypt verifier wiring.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import yaml

from scripts.bcrypt_util import hash_bcrypt, format_iso_timestamp, verify_bcrypt

ROOT = Path(__file__).resolve().parents[1]


def create_mock_cli_env(tmp_path: Path) -> tuple[dict[str, str], Path]:
    """Create a mock CLI environment with fake gcloud, kubectl, and helm."""
    tmp_path = tmp_path.resolve()
    bin_dir = tmp_path / "mock_bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / "mock_state"
    state_dir.mkdir(parents=True, exist_ok=True)

    drive_letter = tmp_path.drive[0].lower() if tmp_path.drive else "c"
    wsl_bin_dir = f"/mnt/{drive_letter}{bin_dir.as_posix()[2:]}" if tmp_path.drive else bin_dir.as_posix()
    wsl_state_dir = f"/mnt/{drive_letter}{state_dir.as_posix()[2:]}" if tmp_path.drive else state_dir.as_posix()

    mock_runner_code = r'''#!/usr/bin/env python3
import sys, os, json, yaml, base64, signal

try:
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
except Exception:
    pass

tool = sys.argv[1]
args = sys.argv[2:]

state_dir = os.environ.get("MOCK_STATE_DIR", ".")
log_file = os.path.join(state_dir, "cli_calls.log")
clusters_file = os.path.join(state_dir, "clusters.txt")
namespaces_file = os.path.join(state_dir, "namespaces.txt")
secrets_file = os.path.join(state_dir, "secrets.yaml")
helm_log = os.path.join(state_dir, "helm_installs.yaml")

with open(log_file, "a", encoding="utf-8") as f:
    f.write(json.dumps({"tool": tool, "args": args}) + "\n")

def load_secrets():
    if os.path.exists(secrets_file):
        with open(secrets_file, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}

def save_secrets(s):
    with open(secrets_file, "w", encoding="utf-8") as f:
        yaml.dump(s, f)

if tool == "gcloud":
    cmd = args[0] if args else ""
    if cmd == "auth":
        print("operator@example.com")
        sys.exit(0)
    elif cmd == "projects":
        if "--project" in args:
            idx = args.index("--project")
            print(args[idx + 1])
        else:
            print("test-project-123")
        sys.exit(0)
    elif cmd == "billing":
        print("True")
        sys.exit(0)
    elif cmd == "iam":
        print("test-node-sa@test-project-123.iam.gserviceaccount.com")
        sys.exit(0)
    elif cmd == "services":
        if "list" in args:
            print("compute.googleapis.com\ncontainer.googleapis.com\nmonitoring.googleapis.com\nlogging.googleapis.com")
        sys.exit(0)
    elif cmd == "compute":
        print("test-resource")
        sys.exit(0)
    elif cmd == "container":
        sub = args[1] if len(args) > 1 else ""
        if sub == "clusters":
            action = args[2] if len(args) > 2 else ""
            if action == "list":
                if os.path.exists(clusters_file):
                    with open(clusters_file, "r", encoding="utf-8") as f:
                        print(f.read().strip())
                else:
                    print("")
                sys.exit(0)
            elif action == "create":
                cname = args[3]
                with open(clusters_file, "w", encoding="utf-8") as f:
                    f.write(f"{cname},us-central1-a\n")
                print(f"Created cluster {cname}")
                sys.exit(0)
            elif action == "describe":
                arg_str = " ".join(args)
                if "format=csv[no-heading](location,autopilot.enabled,workloadIdentityConfig.workloadPool,masterAuthorizedNetworksConfig.enabled)" in arg_str:
                    print("us-central1-a,False,test-project-123.svc.id.goog,True")
                elif "format=value(resourceLabels)" in arg_str:
                    print("environment=development,managed-by=atlasops")
                elif "nodePools[]" in arg_str:
                    print("default-pool,e2-standard-4,test-node-sa@test-project-123.iam.gserviceaccount.com,True,1,3")
                elif "masterAuthorizedNetworksConfig" in arg_str:
                    print("203.0.113.10/32")
                else:
                    print("test-cluster-info")
                sys.exit(0)
            elif action == "get-credentials":
                sys.exit(0)
    elif cmd == "pubsub":
        sys.exit(0)

elif tool == "kubectl":
    if "cluster-info" in args:
        print("Kubernetes control plane is running")
        sys.exit(0)
    elif "create" in args and "namespace" in args:
        ns = args[args.index("namespace") + 1]
        with open(namespaces_file, "a", encoding="utf-8") as f:
            f.write(ns + "\n")
        if "-o" in args and "yaml" in args:
            print(f"apiVersion: v1\nkind: Namespace\nmetadata:\n  name: {ns}")
        sys.exit(0)
    elif "create" in args and "secret" in args and "generic" in args:
        sec_name = args[args.index("generic") + 1]
        ns = "default"
        for a in args:
            if a.startswith("--namespace="):
                ns = a.split("=", 1)[1]
        s_data = {}
        for a in args:
            if a.startswith("--from-file="):
                kv = a.split("=", 1)[1]
                k, fpath = kv.split("=", 1)
                if os.path.exists(fpath):
                    with open(fpath, "r", encoding="utf-8") as ff:
                        s_data[k] = ff.read().strip()
            elif a.startswith("--from-literal="):
                kv = a.split("=", 1)[1]
                k, v = kv.split("=", 1)
                s_data[k] = v
        all_s = load_secrets()
        if ns not in all_s:
            all_s[ns] = {}
        all_s[ns][sec_name] = s_data
        save_secrets(all_s)
        if "-o" in args and "yaml" in args:
            print(f"apiVersion: v1\nkind: Secret\nmetadata:\n  name: {sec_name}\n  namespace: {ns}")
        sys.exit(0)
    elif "get" in args and "secret" in args:
        sec_name = args[args.index("secret") + 1]
        ns = "default"
        for a in args:
            if a.startswith("--namespace="):
                ns = a.split("=", 1)[1]
        all_s = load_secrets()
        s_obj = all_s.get(ns, {}).get(sec_name, {})
        if not s_obj:
            sys.exit(1)
        for a in args:
            if "go-template" in a:
                for k in s_obj:
                    if f'index .data "{k}"' in a or f'index .data \\"{k}\\"' in a:
                        print("present")
                        sys.exit(0)
                sys.exit(0)
        sys.exit(0)
    elif "apply" in args:
        if "-" in args:
            try:
                sys.stdin.read()
            except Exception:
                pass
        sys.exit(0)
    elif "rollout" in args:
        print("deployment successfully rolled out")
        sys.exit(0)

elif tool == "helm":
    if "repo" in args:
        sys.exit(0)
    elif "upgrade" in args:
        release_name = ""
        up_idx = args.index("upgrade")
        for a in args[up_idx + 1:]:
            if not a.startswith("-"):
                release_name = a
                break
        val_files = []
        for i, a in enumerate(args):
            if a == "--values" and i + 1 < len(args):
                vf = args[i + 1]
                if os.path.exists(vf):
                    with open(vf, "r", encoding="utf-8") as vff:
                        val_files.append({"file": vf, "content": yaml.safe_load(vff.read())})
        helm_data = []
        if os.path.exists(helm_log):
            with open(helm_log, "r", encoding="utf-8") as f:
                helm_data = yaml.safe_load(f) or []
        helm_data.append({"release": release_name, "args": args, "values_files": val_files})
        with open(helm_log, "w", encoding="utf-8") as f:
            yaml.dump(helm_data, f)
        sys.exit(0)

sys.exit(0)
'''

    mock_runner = tmp_path / "mock_runner.py"
    with open(mock_runner, "w", encoding="utf-8", newline="\n") as f:
        f.write(mock_runner_code)

    wsl_mock_runner = f"/mnt/{drive_letter}{mock_runner.as_posix()[2:]}" if tmp_path.drive else mock_runner.as_posix()
    for cmd in ["gcloud", "kubectl", "helm"]:
        script_path = bin_dir / cmd
        with open(script_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(f'#!/usr/bin/env bash\npython3 "{wsl_mock_runner}" "{cmd}" "$@"\n')

    # Ensure executable permissions inside bash/WSL
    subprocess.run(["bash", "-c", f'chmod +x "{wsl_bin_dir}"/*'], check=False)

    env_vars = {
        "WSL_BIN_DIR": wsl_bin_dir,
        "MOCK_STATE_DIR": wsl_state_dir,
    }
    return env_vars, state_dir


def prepare_test_secrets(secret_dir: Path, argo_pass: str = "StrongPass-12345!") -> str:
    secret_dir.mkdir(parents=True, exist_ok=True)
    (secret_dir / "atlasops-audit-secret.secret").write_text("audit-tok-123", encoding="utf-8")
    (secret_dir / "alertmanager-webhook-secret.secret").write_text("webhook-tok-456", encoding="utf-8")
    (secret_dir / "atlasops-api-key.secret").write_text("apikey-tok-789", encoding="utf-8")
    (secret_dir / "argocd-user.secret").write_text("atlasops", encoding="utf-8")
    (secret_dir / "argocd-pass.secret").write_text(argo_pass, encoding="utf-8")
    drive_letter = secret_dir.drive[0].lower() if secret_dir.drive else "c"
    return f"/mnt/{drive_letter}{secret_dir.as_posix()[2:]}" if secret_dir.drive else secret_dir.as_posix()


def run_setup(mock_env: dict[str, str], extra_env: dict[str, str], args: list[str]) -> subprocess.CompletedProcess[str]:
    wsl_bin_dir = mock_env["WSL_BIN_DIR"]
    env_exports = [
        f'export PATH="{wsl_bin_dir}:$PATH"',
        f'export MOCK_STATE_DIR="{mock_env["MOCK_STATE_DIR"]}"',
        'export ATLASOPS_BCRYPT_COST="4"',
    ]
    for k, v in extra_env.items():
        env_exports.append(f'export {k}="{v}"')

    arg_str = " ".join(args)
    full_cmd = "; ".join(env_exports) + f"; bash infra/setup_impl.sh {arg_str}"
    return subprocess.run(
        ["bash", "-c", full_cmd],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )


class TestExecutableBootstrapLifecycle:
    """Executable simulated fresh-cluster bootstrap tests using mock CLI boundaries."""

    def test_single_pass_fresh_deployment_success(self, tmp_path: Path) -> None:
        """Proves a brand-new empty cluster executes in ONE single pass without failure."""
        mock_env, state_dir = create_mock_cli_env(tmp_path)
        secret_dir = tmp_path / "secrets"
        wsl_secret_dir = prepare_test_secrets(secret_dir, argo_pass="MyArgoPassword123!")

        extra_env = {
            "ATLASOPS_GKE_NODE_SERVICE_ACCOUNT": "test-node-sa@test-project-123.iam.gserviceaccount.com",
            "ATLASOPS_GKE_AUTHORIZED_NETWORKS": "203.0.113.10/32",
            "ATLASOPS_COORDINATOR_IMAGE": "us-central1-docker.pkg.dev/test-project-123/atlasops/atlasops-coordinator@sha256:" + "a" * 64,
            "ATLASOPS_VLLM_BASE": "http://vllm-backend:8000/v1",
            "ATLASOPS_AGENT_MODEL": "Qwen/Qwen2.5-7B-Instruct",
            "ATLASOPS_COST_ACK": "I_UNDERSTAND_GCP_COSTS",
            "ATLASOPS_SECRET_DIR": wsl_secret_dir,
        }

        # Run single-pass apply on fresh cluster
        res = run_setup(mock_env, extra_env, ["test-project-123", "us-central1", "atlasops", "--apply"])
        assert res.returncode == 0, f"setup.sh failed: {res.stderr}\nstdout: {res.stdout}"

        # 1. Inspect call sequence log
        log_file = state_dir / "cli_calls.log"
        assert log_file.is_file()
        lines = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines()]

        # Verify cluster creation happened
        cluster_creates = [x for x in lines if x["tool"] == "gcloud" and "container" in x["args"] and "create" in x["args"]]
        assert len(cluster_creates) == 1, "GKE cluster was not created"
        cluster_create_idx = lines.index(cluster_creates[0])

        # Verify namespaces created AFTER cluster
        ns_creates = [x for x in lines if x["tool"] == "kubectl" and "create" in x["args"] and "namespace" in x["args"]]
        assert len(ns_creates) >= 5, "Namespaces were not created"
        first_ns_idx = lines.index(ns_creates[0])
        assert cluster_create_idx < first_ns_idx, "Namespaces must be created after cluster"

        # Verify Kubernetes secrets created AFTER namespaces
        sec_creates = [x for x in lines if x["tool"] == "kubectl" and "create" in x["args"] and "secret" in x["args"]]
        assert len(sec_creates) >= 2, "Kubernetes Secrets were not provisioned"
        first_sec_idx = lines.index(sec_creates[0])
        assert first_ns_idx < first_sec_idx, "Secrets must be provisioned after namespaces"

        # Verify Helm installs occurred
        helm_installs = [x for x in lines if x["tool"] == "helm" and "upgrade" in x["args"]]
        assert len(helm_installs) >= 4, "Helm charts were not installed"

        # Verify Argo CD received bcrypt password verifier overlay
        helm_log_file = state_dir / "helm_installs.yaml"
        assert helm_log_file.is_file()
        helm_log = yaml.safe_load(helm_log_file.read_text(encoding="utf-8"))
        argo_entry = [entry for entry in helm_log if entry["release"] == "argocd"][0]
        assert len(argo_entry["values_files"]) >= 2, "Argo CD did not receive overlay values"

        # Verify overlay contents
        overlay_val = argo_entry["values_files"][1]["content"]
        assert "configs" in overlay_val and "secret" in overlay_val["configs"]
        extra = overlay_val["configs"]["secret"]["extra"]
        assert "accounts.atlasops.password" in extra
        hashed_pass = extra["accounts.atlasops.password"]
        assert hashed_pass.startswith("$2a$")
        assert len(hashed_pass) == 60
        assert "accounts.atlasops.passwordMtime" in extra
        assert verify_bcrypt("MyArgoPassword123!", hashed_pass)

        # Verify coordinator rollout happened at the end
        coord_rollout = [x for x in lines if x["tool"] == "kubectl" and "rollout" in x["args"] and "deployment/atlasops-coordinator" in " ".join(x["args"])]
        assert len(coord_rollout) == 1, "Coordinator rollout was not waited for"

    def test_missing_local_secret_fails_before_cloud_mutation(self, tmp_path: Path) -> None:
        """Proves missing local secrets abort immediately BEFORE any gcloud mutation."""
        mock_env, state_dir = create_mock_cli_env(tmp_path)
        secret_dir = tmp_path / "secrets_incomplete"
        secret_dir.mkdir(parents=True, exist_ok=True)
        (secret_dir / "atlasops-audit-secret.secret").write_text("audit-tok", encoding="utf-8")
        drive_letter = secret_dir.drive[0].lower() if secret_dir.drive else "c"
        wsl_secret_dir = f"/mnt/{drive_letter}{secret_dir.as_posix()[2:]}" if secret_dir.drive else secret_dir.as_posix()

        extra_env = {
            "ATLASOPS_GKE_NODE_SERVICE_ACCOUNT": "test-node-sa@test-project-123.iam.gserviceaccount.com",
            "ATLASOPS_GKE_AUTHORIZED_NETWORKS": "203.0.113.10/32",
            "ATLASOPS_COORDINATOR_IMAGE": "us-central1-docker.pkg.dev/test-project-123/atlasops/atlasops-coordinator@sha256:" + "a" * 64,
            "ATLASOPS_VLLM_BASE": "http://vllm-backend:8000/v1",
            "ATLASOPS_AGENT_MODEL": "Qwen/Qwen2.5-7B-Instruct",
            "ATLASOPS_COST_ACK": "I_UNDERSTAND_GCP_COSTS",
            "ATLASOPS_SECRET_DIR": wsl_secret_dir,
        }

        res = run_setup(mock_env, extra_env, ["test-project-123", "us-central1", "atlasops", "--apply"])
        assert res.returncode != 0
        assert "Missing required local secret file" in res.stderr

        # Assert ZERO cloud mutation commands were executed
        log_file = state_dir / "cli_calls.log"
        if log_file.is_file():
            calls = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines()]
            mutations = [c for c in calls if c["tool"] == "gcloud" and any(k in c["args"] for k in ["create", "enable", "delete"])]
            assert len(mutations) == 0, f"Cloud mutation occurred despite missing secret: {mutations}"

    def test_missing_argocd_password_fails_before_cloud_mutation(self, tmp_path: Path) -> None:
        """Proves missing Argo CD password aborts before cloud mutation when Argo is enabled."""
        mock_env, state_dir = create_mock_cli_env(tmp_path)
        secret_dir = tmp_path / "secrets_no_argo"
        secret_dir.mkdir(parents=True, exist_ok=True)
        (secret_dir / "atlasops-audit-secret.secret").write_text("audit-tok", encoding="utf-8")
        (secret_dir / "alertmanager-webhook-secret.secret").write_text("webhook-tok", encoding="utf-8")
        (secret_dir / "atlasops-api-key.secret").write_text("api-tok", encoding="utf-8")
        (secret_dir / "argocd-user.secret").write_text("atlasops", encoding="utf-8")
        drive_letter = secret_dir.drive[0].lower() if secret_dir.drive else "c"
        wsl_secret_dir = f"/mnt/{drive_letter}{secret_dir.as_posix()[2:]}" if secret_dir.drive else secret_dir.as_posix()

        extra_env = {
            "ATLASOPS_GKE_NODE_SERVICE_ACCOUNT": "test-node-sa@test-project-123.iam.gserviceaccount.com",
            "ATLASOPS_GKE_AUTHORIZED_NETWORKS": "203.0.113.10/32",
            "ATLASOPS_COORDINATOR_IMAGE": "us-central1-docker.pkg.dev/test-project-123/atlasops/atlasops-coordinator@sha256:" + "a" * 64,
            "ATLASOPS_VLLM_BASE": "http://vllm-backend:8000/v1",
            "ATLASOPS_AGENT_MODEL": "Qwen/Qwen2.5-7B-Instruct",
            "ATLASOPS_COST_ACK": "I_UNDERSTAND_GCP_COSTS",
            "ATLASOPS_ENABLE_ARGOCD": "true",
            "ATLASOPS_SECRET_DIR": wsl_secret_dir,
        }

        res = run_setup(mock_env, extra_env, ["test-project-123", "us-central1", "atlasops", "--apply"])
        assert res.returncode != 0
        assert "argocd-pass.secret" in res.stderr

    def test_existing_compatible_cluster_reuse_is_idempotent(self, tmp_path: Path) -> None:
        """Proves re-running setup on an existing compatible cluster does not re-create GKE."""
        mock_env, state_dir = create_mock_cli_env(tmp_path)
        secret_dir = tmp_path / "secrets"
        wsl_secret_dir = prepare_test_secrets(secret_dir)

        # Pre-seed existing cluster in mock state
        clusters_file = state_dir / "clusters.txt"
        clusters_file.write_text("atlasops,us-central1-a\n", encoding="utf-8")

        extra_env = {
            "ATLASOPS_GKE_NODE_SERVICE_ACCOUNT": "test-node-sa@test-project-123.iam.gserviceaccount.com",
            "ATLASOPS_GKE_AUTHORIZED_NETWORKS": "203.0.113.10/32",
            "ATLASOPS_COORDINATOR_IMAGE": "us-central1-docker.pkg.dev/test-project-123/atlasops/atlasops-coordinator@sha256:" + "a" * 64,
            "ATLASOPS_VLLM_BASE": "http://vllm-backend:8000/v1",
            "ATLASOPS_AGENT_MODEL": "Qwen/Qwen2.5-7B-Instruct",
            "ATLASOPS_COST_ACK": "I_UNDERSTAND_GCP_COSTS",
            "ATLASOPS_SECRET_DIR": wsl_secret_dir,
        }

        res = run_setup(mock_env, extra_env, ["test-project-123", "us-central1", "atlasops", "--apply"])
        assert res.returncode == 0
        assert "reusing existing verified cluster" in res.stdout

        # Verify gcloud container clusters create was NEVER called
        log_file = state_dir / "cli_calls.log"
        lines = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines()]
        creates = [c for c in lines if c["tool"] == "gcloud" and "container" in c["args"] and "create" in c["args"]]
        assert len(creates) == 0, "Cluster was re-created on existing compatible cluster"

    def test_argo_disabled_deviation_skips_argo_resources(self, tmp_path: Path) -> None:
        """Proves setting ATLASOPS_ENABLE_ARGOCD=false skips Argo namespace, secrets, and chart."""
        mock_env, state_dir = create_mock_cli_env(tmp_path)
        secret_dir = tmp_path / "secrets_no_argo"
        secret_dir.mkdir(parents=True, exist_ok=True)
        (secret_dir / "atlasops-audit-secret.secret").write_text("audit-tok", encoding="utf-8")
        (secret_dir / "alertmanager-webhook-secret.secret").write_text("webhook-tok", encoding="utf-8")
        (secret_dir / "atlasops-api-key.secret").write_text("api-tok", encoding="utf-8")
        drive_letter = secret_dir.drive[0].lower() if secret_dir.drive else "c"
        wsl_secret_dir = f"/mnt/{drive_letter}{secret_dir.as_posix()[2:]}" if secret_dir.drive else secret_dir.as_posix()

        extra_env = {
            "ATLASOPS_GKE_NODE_SERVICE_ACCOUNT": "test-node-sa@test-project-123.iam.gserviceaccount.com",
            "ATLASOPS_GKE_AUTHORIZED_NETWORKS": "203.0.113.10/32",
            "ATLASOPS_COORDINATOR_IMAGE": "us-central1-docker.pkg.dev/test-project-123/atlasops/atlasops-coordinator@sha256:" + "a" * 64,
            "ATLASOPS_VLLM_BASE": "http://vllm-backend:8000/v1",
            "ATLASOPS_AGENT_MODEL": "Qwen/Qwen2.5-7B-Instruct",
            "ATLASOPS_COST_ACK": "I_UNDERSTAND_GCP_COSTS",
            "ATLASOPS_ENABLE_ARGOCD": "false",
            "ATLASOPS_SECRET_DIR": wsl_secret_dir,
        }

        res = run_setup(mock_env, extra_env, ["test-project-123", "us-central1", "atlasops", "--apply"])
        assert res.returncode == 0
        assert "DEVIATION: canonical Gate G3 cannot PASS without Argo CD" in res.stdout

        # Verify argocd namespace was NOT created
        namespaces_file = state_dir / "namespaces.txt"
        namespaces = namespaces_file.read_text(encoding="utf-8").splitlines()
        assert "argocd" not in namespaces


class TestBcryptUtilContract:
    """Tests mathematical bcrypt Blowfish derivation and password verification."""

    def test_bcrypt_derivation_and_verification(self) -> None:
        pwd = "test-operator-password-xyz"
        hashed = hash_bcrypt(pwd, cost=4)
        assert hashed.startswith("$2a$04$")
        assert len(hashed) == 60
        assert verify_bcrypt(pwd, hashed) is True
        assert verify_bcrypt("wrong-password", hashed) is False

    def test_iso_timestamp_format(self) -> None:
        ts = format_iso_timestamp()
        assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", ts)


class TestSecretGeneratorContract:
    """Tests safety and correctness of scripts/generate_runtime_secrets.py."""

    def test_secret_generator_execution_and_randomness(self, tmp_path: Path) -> None:
        from scripts.generate_runtime_secrets import generate_token, main
        import sys

        out_dir = tmp_path / "test_secrets"
        tok1 = generate_token(32)
        tok2 = generate_token(32)
        assert len(tok1) == 64
        assert len(tok2) == 64
        assert tok1 != tok2

        pass_file = tmp_path / "mypass.txt"
        pass_file.write_text("my-strong-argo-password", encoding="utf-8")

        test_args = ["generate_runtime_secrets.py", "--output-dir", str(out_dir), "--argocd-user", "atlasops", "--argocd-pass-file", str(pass_file)]
        sys_argv_orig = sys.argv
        try:
            sys.argv = test_args
            main()
        finally:
            sys.argv = sys_argv_orig

        assert (out_dir / "atlasops-audit-secret.secret").is_file()
        assert (out_dir / "alertmanager-webhook-secret.secret").is_file()
        assert (out_dir / "atlasops-api-key.secret").is_file()
        assert (out_dir / "argocd-user.secret").is_file()
        assert (out_dir / "argocd-pass.secret").is_file()
        assert (out_dir / "apply_secrets.sh").is_file()

        assert (out_dir / "argocd-user.secret").read_text(encoding="utf-8").strip() == "atlasops"
        assert (out_dir / "argocd-pass.secret").read_text(encoding="utf-8").strip() == "my-strong-argo-password"

        script = (out_dir / "apply_secrets.sh").read_text(encoding="utf-8")
        assert "<ARGOCD_USER>" not in script
        assert "<ARGOCD_PASSWORD>" not in script
        assert "--from-file=argocd-user=" in script
        assert "--from-file=argocd-pass=" in script
        assert "--context" in script
        assert "Ambient kubectl contexts are intentionally rejected" in script
