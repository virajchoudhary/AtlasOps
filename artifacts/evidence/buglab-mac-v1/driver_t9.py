"""T9 — rotate the model endpoint and record what the lab needs in order to follow.

The lab detects its backend once, at startup (demo/bug_lab/llm.py probes GET
/models during create_app), so a URL change is not picked up by a running
container. This records that behaviour rather than working around it: it moves
the proxy to a new port, shows the old container still pointing at the dead
endpoint, then recreates the container against the new URL and shows it healthy.

Run from the repository root. Needs ~/atlasops-local/buglab.env.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8790"
ENV = pathlib.Path.home() / "atlasops-local" / "buglab.env"
PROXY_ENV = pathlib.Path.home() / "atlasops-local" / "proxy.env"
OLD_PORT, NEW_PORT = 8000, 8001


def sh(*args: str) -> str:
    done = subprocess.run(args, capture_output=True, text=True)
    return (done.stdout + done.stderr).strip()


def state() -> dict:
    try:
        with urllib.request.urlopen(BASE + "/lab/api/state", timeout=15) as r:
            return json.loads(r.read() or b"{}")
    except Exception as exc:
        return {"_error": f"{exc.__class__.__name__}: {exc}"}


def key() -> str:
    for line in ENV.read_text().splitlines():
        if line.startswith("BUG_LAB_LLM_API_KEY="):
            return line.split("=", 1)[1]
    raise SystemExit("no key in env file")


def probe(port: int) -> str:
    req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/models",
                                 headers={"Authorization": f"Bearer {key()}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return f"HTTP {r.status}"
    except urllib.error.HTTPError as exc:
        return f"HTTP {exc.code}"
    except Exception as exc:
        return exc.__class__.__name__


def write_env(port: int) -> None:
    lines = []
    for line in ENV.read_text().splitlines():
        if line.startswith("BUG_LAB_LLM_BASE_URL="):
            line = f"BUG_LAB_LLM_BASE_URL=http://host.docker.internal:{port}/v1"
        lines.append(line)
    ENV.write_text("\n".join(lines) + "\n")
    os.chmod(ENV, 0o600)


def run_lab() -> str:
    sh("docker", "rm", "-f", "buglab")
    return sh("docker", "run", "-d", "--name", "buglab",
              "-p", "127.0.0.1:8790:8790", "--env-file", str(ENV),
              "atlasops-buglab:local",
              "python", "-m", "demo.bug_lab", "--host", "0.0.0.0", "--allow-remote",
              "--workdir", "/work", "--agents", "llm")


def main() -> None:
    rec: dict = {"generated_at": time.time(), "old_port": OLD_PORT, "new_port": NEW_PORT}
    rec["backend_before"] = state().get("backend")
    rec["old_endpoint_before"] = probe(OLD_PORT)

    # Move the proxy to the new port. Same image, same key, loopback only.
    sh("docker", "rm", "-f", "buglab-llm-proxy")
    rec["proxy_run"] = sh("docker", "run", "-d", "--name", "buglab-llm-proxy",
                          "-p", f"127.0.0.1:{NEW_PORT}:{NEW_PORT}",
                          "--env-file", str(PROXY_ENV),
                          "-v", f"{os.getcwd()}/scripts/local_llm_proxy.py:/app/proxy.py:ro",
                          "atlasops-buglab:local",
                          "python", "-m", "uvicorn", "proxy:app",
                          "--host", "0.0.0.0", "--port", str(NEW_PORT))[:64]
    time.sleep(6.0)
    rec["old_endpoint_after_move"] = probe(OLD_PORT)
    rec["new_endpoint_after_move"] = probe(NEW_PORT)

    # The running container still holds the old URL and has no way to learn the new one.
    rec["backend_still_reported_by_running_container"] = state().get("backend")
    rec["lab_call_after_move"] = probe(OLD_PORT)

    # Rotate the env file and recreate the container, which is the documented step.
    write_env(NEW_PORT)
    rec["container_id_after_restart"] = run_lab()[:12]
    time.sleep(12.0)
    rec["backend_after_restart"] = state().get("backend")
    rec["lab_log_after_restart"] = sh("docker", "logs", "--tail", "10", "buglab")

    out = sys.argv[1] if len(sys.argv) > 1 else "t9_rotation.json"
    with open(out, "w") as fh:
        json.dump(rec, fh, indent=2)
    print(json.dumps({k: rec.get(k) for k in
                      ("old_endpoint_after_move", "new_endpoint_after_move",
                       "backend_still_reported_by_running_container",
                       "backend_after_restart")}, indent=2), flush=True)
    print("wrote", out, flush=True)


if __name__ == "__main__":
    main()
