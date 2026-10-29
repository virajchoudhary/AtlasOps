"""T8 (v2) — take the model away mid-incident and record what the lab does.

Same as the v1 driver, with two additions for the fixes in 9ab930a: the lab's
log is captured either side of the outage so the model->rules fallback lines can
be shown rather than asserted, and the new incident_state / incident_sequence
fields are recorded at every poll.

Injects a bug, waits until an incident is open and the model has started work,
then stops the proxy container so every further model call fails. Nothing is
assumed about the outcome: the final state is read back from /lab/api/state.
The proxy is restarted at the end regardless of what happened.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8790"
HEAD = {"X-Bug-Lab": "1", "Content-Type": "application/json"}
BUG = "truncated-data"
PROXY = "buglab-llm-proxy"


def call(path: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data,
                                 headers=HEAD if data else {}, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return {"_http_error": exc.code, "_detail": exc.read().decode()[:300]}


def state() -> dict:
    return call("/lab/api/state")


def store_status() -> int:
    try:
        with urllib.request.urlopen(BASE + "/shop/api/products", timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as exc:
        return exc.code


def docker(*args: str) -> str:
    # `docker logs` writes the container's stderr to OUR stderr, and the lab's
    # warnings go to stderr. The first run of this driver read stdout only and so
    # reported an empty log; that was this driver's bug, not the lab's.
    done = subprocess.run(["docker", *args], capture_output=True, text=True)
    return (done.stdout + done.stderr).strip()


def wait_idle(limit: float = 90.0) -> bool:
    end = time.time() + limit
    while time.time() < end:
        snap = state()
        inc = snap.get("incident") or {}
        if not snap.get("active") and inc.get("status") in (None, "closed", "resolved", "escalated"):
            return True
        time.sleep(2.0)
    return False


def main() -> None:
    rec: dict = {"bug": BUG, "generated_at": time.time()}
    rec["idle_before_start"] = wait_idle()
    rec["reset_response"] = call("/lab/api/reset", {})
    time.sleep(2.0)
    rec["backend_before"] = state().get("backend")
    # The previous incident stays in /lab/api/state after it closes, so the id is
    # recorded first and anything still carrying it is ignored. Without this the
    # test latches onto the closed incident and cuts the model at stage "closed".
    before_id = (state().get("incident") or {}).get("id")
    rec["incident_before_inject"] = before_id
    rec["inject_response"] = call("/lab/api/inject", {"bug": BUG})

    # Wait for a NEW incident, open and past detection, so the outage lands while
    # the model is actually being called.
    opened = None
    end = time.time() + 120
    while time.time() < end:
        time.sleep(2.0)
        inc = state().get("incident") or {}
        if (inc.get("id") and inc.get("id") != before_id
                and inc.get("stage") not in (None, "monitor")):
            opened = inc
            break
    if not opened:
        rec["error"] = "no new incident progressed past monitor"
        print(json.dumps(rec, indent=2), flush=True)
        return
    rec["incident"] = opened.get("id")
    rec["stage_when_cut"] = opened.get("stage")
    print(f"cutting the model at stage={opened.get('stage')}", flush=True)

    rec["log_lines_before_cut"] = len(docker("logs", "buglab").splitlines())
    rec["proxy_stop_output"] = docker("stop", PROXY)
    rec["cut_at"] = time.time()
    # Confirm the endpoint really is gone.
    try:
        urllib.request.urlopen("http://127.0.0.1:8000/v1/models", timeout=5)
        rec["endpoint_after_cut"] = "still answering"
    except Exception as exc:
        rec["endpoint_after_cut"] = f"{exc.__class__.__name__}"

    observed = []
    started = time.time()
    rec["approved"] = False
    while time.time() - started < 420:
        time.sleep(10.0)
        snap = state()
        inc = snap.get("incident") or {}
        # Approve when asked, otherwise the approval TTL expires and that, not the
        # outage, becomes the outcome. The first run of this test made that mistake.
        ap = inc.get("approval") or {}
        if ap.get("required") and ap.get("decision") is None and ap.get("digest"):
            rec["decision_sent"] = call("/lab/api/approval",
                                        {"incident_id": inc.get("id"),
                                         "digest": ap["digest"], "decision": "approve"})
            rec["approved"] = True
        observed.append({"t": round(time.time() - started), "status": inc.get("status"),
                         "stage": inc.get("stage"), "outcome": inc.get("outcome"),
                         "active": snap.get("active"),
                         "incident_state": snap.get("incident_state"),
                         "incident_sequence": snap.get("incident_sequence")})
        print(f"  t={observed[-1]['t']}s status={observed[-1]['status']} "
              f"stage={observed[-1]['stage']}", flush=True)
        if not snap.get("active") and inc.get("status") in ("resolved", "closed", "escalated",
                                                            "failed"):
            break

    snap = state()
    final = snap.get("incident") or {}
    rec["observed"] = observed
    rec["final_status"] = final.get("status")
    rec["final_outcome"] = final.get("outcome")
    rec["final_stage"] = final.get("stage")
    rec["resolved_by"] = final.get("resolved_by")
    rec["patch_author"] = (final.get("patch") or {}).get("author")
    rec["comms"] = final.get("comms")
    rec["store_status_at_end"] = store_status()
    full_log = docker("logs", "buglab").splitlines()
    rec["log_since_cut"] = full_log[rec["log_lines_before_cut"]:]
    rec["fallback_log_lines"] = [l for l in rec["log_since_cut"] if "Model fallback" in l]
    rec["incident_events"] = [e for e in (final.get("events") or [])
                              if e.get("kind") == "warning"]

    rec["proxy_start_output"] = docker("start", PROXY)
    time.sleep(5.0)
    try:
        urllib.request.urlopen("http://127.0.0.1:8000/v1/models", timeout=5)
        rec["endpoint_after_restore"] = "answering (401 expected without key)"
    except urllib.error.HTTPError as exc:
        rec["endpoint_after_restore"] = f"HTTP {exc.code} (401 = proxy back up)"
    except Exception as exc:
        rec["endpoint_after_restore"] = f"still down: {exc.__class__.__name__}"

    out = sys.argv[1] if len(sys.argv) > 1 else "t8_outage.json"
    with open(out, "w") as fh:
        json.dump(rec, fh, indent=2)
    print(json.dumps({k: rec.get(k) for k in
                      ("incident", "stage_when_cut", "final_status", "final_outcome",
                       "store_status_at_end", "endpoint_after_restore")}, indent=2), flush=True)
    print("wrote", out, flush=True)


if __name__ == "__main__":
    main()
