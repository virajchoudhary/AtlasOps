"""T9 on v4, isolated — rotate the model endpoint and record what the lab needs.

Mirrors artifacts/evidence/buglab-mac-v1/driver_t9.py, with the live stack taken
out of the blast radius. The original does `docker rm -f buglab` and rewrites
~/atlasops-local/buglab.env; this one moves a dedicated proxy (8001 -> 8002) and
recreates only the dedicated lab container on 8793. The live lab on 8790, its
proxy on 8000, and the env file are untouched.

The claim under test is the documented one: demo/bug_lab/llm.py probes GET
/models once during create_app, so a running container cannot learn a new URL.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8793"
OLD_PORT = 8001
NEW_PORT = 8002
LAB = "buglab-t8-llm"
OLD_PROXY = "buglab-t8-proxy"
NEW_PROXY = "buglab-t9-proxy"
IMAGE = "atlasops-buglab:v4-resume"
PROXY_IMAGE = "atlasops-buglab:local"
KEY = open(os.path.expanduser(
    "/private/tmp/claude-501/-Users-dhruvaambhaikar/"
    "e5658cdf-65c1-41f0-8efa-5746786dd6a6/scratchpad/acc/t8key")).read().strip()


def sh(*args: str) -> str:
    done = subprocess.run(["docker", *args], capture_output=True, text=True)
    return (done.stdout or done.stderr).strip()


def state() -> dict:
    with urllib.request.urlopen(BASE + "/lab/api/state", timeout=20) as r:
        return json.loads(r.read())


def probe(port: int) -> str:
    """Is a proxy answering on this port? 401 without the key means yes."""
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/models", timeout=8)
        return "answering (unexpectedly unauthenticated)"
    except urllib.error.HTTPError as exc:
        return f"HTTP {exc.code} (401 = proxy up, key enforced)"
    except Exception as exc:
        return f"{exc.__class__.__name__} (nothing listening)"


def run_lab(port: int) -> str:
    sh("rm", "-f", LAB)
    return sh("run", "-d", "--name", LAB, "-p", "127.0.0.1:8793:8790",
              "-e", f"BUG_LAB_LLM_BASE_URL=http://host.docker.internal:{port}/v1",
              "-e", "BUG_LAB_LLM_MODEL=qwen2.5:7b-instruct",
              "-e", f"BUG_LAB_LLM_API_KEY={KEY}",
              "-e", "BUG_LAB_LLM_TIMEOUT=120",
              IMAGE, "--agents", "llm")


def main() -> None:
    rec: dict = {"generated_at": time.time(), "old_port": OLD_PORT,
                 "new_port": NEW_PORT, "isolated_from_live_stack": True}
    rec["backend_before"] = state().get("backend")
    rec["old_endpoint_before"] = probe(OLD_PORT)
    rec["new_endpoint_before"] = probe(NEW_PORT)

    # Move the proxy to the new port. Same image, same key, loopback only.
    sh("rm", "-f", OLD_PROXY)
    sh("rm", "-f", NEW_PROXY)
    rec["proxy_run"] = sh("run", "-d", "--name", NEW_PROXY,
                          "-p", f"127.0.0.1:{NEW_PORT}:{NEW_PORT}",
                          "-e", f"PROXY_KEY={KEY}",
                          "-e", "OLLAMA_BASE_URL=http://host.docker.internal:11434",
                          "-v", f"{os.getcwd()}/scripts/local_llm_proxy.py:/app/proxy.py:ro",
                          PROXY_IMAGE, "python", "-m", "uvicorn", "proxy:app",
                          "--host", "0.0.0.0", "--port", str(NEW_PORT))[:64]
    time.sleep(8.0)
    rec["old_endpoint_after_move"] = probe(OLD_PORT)
    rec["new_endpoint_after_move"] = probe(NEW_PORT)

    # The running container still holds the old URL and cannot learn the new one.
    rec["backend_still_reported_by_running_container"] = state().get("backend")

    # Recreate the container against the new URL: the documented step.
    rec["container_id_after_restart"] = run_lab(NEW_PORT)[:12]
    time.sleep(15.0)
    rec["backend_after_restart"] = state().get("backend")
    rec["lab_log_after_restart"] = sh("logs", "--tail", "8", LAB)
    rec["live_lab_8790_still_up"] = subprocess.run(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
         "--max-time", "10", "http://127.0.0.1:8790/"],
        capture_output=True, text=True).stdout.strip()

    out = sys.argv[1] if len(sys.argv) > 1 else "t9_rotation.json"
    with open(out, "w") as fh:
        json.dump(rec, fh, indent=2)
    for k in ("old_endpoint_before", "old_endpoint_after_move", "new_endpoint_after_move",
              "container_id_after_restart", "live_lab_8790_still_up"):
        print(f"  {k:42} {rec.get(k)}")
    print("  backend before  :", (rec["backend_before"] or {}).get("base_url"))
    print("  backend stale   :", (rec["backend_still_reported_by_running_container"] or {}).get("base_url"))
    print("  backend after   :", (rec["backend_after_restart"] or {}).get("base_url"))
    print("wrote", out)


if __name__ == "__main__":
    main()
