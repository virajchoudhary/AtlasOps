"""Drive the bug lab through every catalogued bug and record what actually happened.

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
TIMEOUT = 240.0
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
    call("/lab/api/reset")
    time.sleep(1.0)
    started = time.time()
    injected = call("/lab/api/inject", {"bug": bug})
    record = {"bug": bug, "inject_response": injected, "approved": False,
              "decision_sent": None, "started_incident": False}
    deadline = started + TIMEOUT
    last = {}
    while time.time() < deadline:
        time.sleep(POLL)
        snap = state()
        inc = snap.get("incident") or {}
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
    return record


def main() -> None:
    bugs = [b["id"] for b in state()["catalog"]]
    print(f"catalogued bugs: {len(bugs)}", flush=True)
    results = []
    for bug in bugs:
        print(f"--- {bug}", flush=True)
        rec = run_one(bug)
        print(f"    status={rec['final']['status']} outcome={rec['final']['outcome']} "
              f"approved={rec['approved']} started={rec['started_incident']} "
              f"{rec['elapsed_s']}s", flush=True)
        results.append(rec)
    out = sys.argv[1] if len(sys.argv) > 1 else "t5_results.json"
    with open(out, "w") as fh:
        json.dump({"base": BASE, "generated_at": time.time(), "results": results}, fh, indent=2)
    print(f"wrote {out}", flush=True)


if __name__ == "__main__":
    main()
