"""T7 (v2) — drive every catalogued bug with the model backend and record what happened.

Updated for 9ab930a. The v1 driver guarded against the stale-incident problem by
remembering the previous incident id; this one waits on incident_sequence, the
counter that commit added, and records incident_state at each decision point so
the new fields are exercised rather than trusted. It also keeps the browser check
results for each bug: three of the eight are only visible to the in-page monitor,
so a real browser must be open on /shop/ for them to be detected and verified.

Same driver as T5 but against --agents llm, with a longer per-bug deadline because
a 7B on an M4 answers far slower than the rule engine. Records the backend label
and the patch author so the model-versus-rule contribution is visible per bug.

Writes one JSON record per bug. Nothing here invents a result: every field is read
back from /lab/api/state.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8790"
HEAD = {"X-Bug-Lab": "1", "Content-Type": "application/json"}
TIMEOUT = 480.0
POLL = 2.0


def call(path: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data,
                                 headers=HEAD if data else {}, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return {"_http_error": exc.code, "_detail": exc.read().decode()[:200]}


def state() -> dict:
    return call("/lab/api/state")


def run_one(bug: str, approve: bool = True) -> dict:
    # /lab/api/reset is POST and requires X-Bug-Lab; call() only sends the header
    # when a payload is present, so it must be given one.
    reset = call("/lab/api/reset", {})
    time.sleep(2.0)
    # The previous incident stays in /lab/api/state after closing, so its id is
    # recorded and anything still carrying it is ignored as stale.
    before = state()
    before_id = (before.get("incident") or {}).get("id")
    before_seq = before.get("incident_sequence")
    started = time.time()
    injected = call("/lab/api/inject", {"bug": bug})
    record = {"bug": bug, "reset_response": reset, "incident_before_inject": before_id,
              "incident_state_after_reset": before.get("incident_state"),
              "incident_sequence_before": before_seq,
              "inject_response": injected, "approved": False,
              "decision_sent": None, "started_incident": False, "state_trace": []}
    deadline = started + TIMEOUT
    last = {}
    while time.time() < deadline:
        time.sleep(POLL)
        snap = state()
        inc = snap.get("incident") or {}
        seq = snap.get("incident_sequence")
        if not record["state_trace"] or record["state_trace"][-1][1] != snap.get("incident_state"):
            record["state_trace"].append([round(time.time() - started, 1),
                                          snap.get("incident_state"), seq,
                                          inc.get("id") or None])
        if inc and inc.get("id") == before_id:
            continue  # stale: the previous incident, not this one
        if seq is not None and before_seq is not None and seq == before_seq and not inc:
            continue  # the new incident has not opened yet
        if inc:
            record["started_incident"] = True
            last = inc
        approval = inc.get("approval") or {}
        if (approve and approval.get("required") and approval.get("decision") is None
                and approval.get("digest")):
            out = call("/lab/api/approval", {"incident_id": inc.get("id"),
                                             "digest": approval["digest"],
                                             "decision": "approve"})
            record["approved"] = True
            record["decision_sent"] = out
            continue
        if inc and not snap.get("active") and inc.get("status") in {"resolved", "closed", "failed"}:
            break
        if not inc and not snap.get("active") and time.time() - started > 45:
            break
    record["elapsed_s"] = round(time.time() - started, 1)
    record["final"] = {k: last.get(k) for k in
                       ("id", "status", "stage", "severity", "outcome", "resolved_by",
                        "diagnosis_matched", "detect_s", "resolve_s", "attempts")}
    record["diagnosis"] = last.get("diagnosis")
    record["patch_author"] = (last.get("patch") or {}).get("author")
    record["alerts_failing_at_end"] = [a.get("name") for a in (last.get("alerts") or [])
                                       if a.get("ok") is False]
    end = state()
    checks = end.get("checks") or {}
    record["incident_sequence_after"] = end.get("incident_sequence")
    record["incident_state_at_end"] = end.get("incident_state")
    # Real browser evidence: these come from the page's own monitor posting to
    # /lab/api/telemetry, so they are only present when a browser is on /shop/.
    record["browser_checks_at_end"] = [{"name": c.get("name"), "ok": c.get("ok")}
                                       for c in (checks.get("browser") or [])]
    record["browser_current"] = checks.get("browser_current")
    record["browser_stale"] = checks.get("browser_stale")
    record["server_checks_at_end"] = [{"name": c.get("name"), "ok": c.get("ok")}
                                      for c in (checks.get("server") or [])]
    return record


def main() -> None:
    bugs = [b["id"] for b in state()["catalog"]]
    print("backend:", json.dumps(state().get("backend")), flush=True)
    print(f"catalogued bugs: {len(bugs)}", flush=True)
    results = []
    for bug in bugs:
        print(f"--- {bug}", flush=True)
        rec = run_one(bug)
        print(f"    status={rec['final']['status']} outcome={rec['final']['outcome']} "
              f"approved={rec['approved']} started={rec['started_incident']} "
              f"{rec['elapsed_s']}s", flush=True)
        results.append(rec)
    out = sys.argv[1] if len(sys.argv) > 1 else "t7_results.json"
    with open(out, "w") as fh:
        json.dump({"base": BASE, "generated_at": time.time(),
                   "backend": state().get("backend"), "results": results}, fh, indent=2)
    print(f"wrote {out}", flush=True)


if __name__ == "__main__":
    main()
