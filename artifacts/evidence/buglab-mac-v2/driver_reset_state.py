"""Reset and incident tracking: can a naive poller still read a stale result?

Finding 5 from the v1 acceptance was that /lab/api/state kept serving the last
closed incident with nothing marking it stale, so a poller that did not track
incident ids read the previous result as the new one. 9ab930a adds
incident_state and incident_sequence and clears incident on reset and on a
successful injection. This checks those four transitions against the API rather
than against the release notes.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8790"
HEAD = {"X-Bug-Lab": "1", "Content-Type": "application/json"}
BUG = "truncated-data"  # server-detected, needs no browser


def call(path: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data,
                                 headers=HEAD if data else {}, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return {"_http_error": exc.code, "_detail": exc.read().decode()[:300]}


def probe(label: str) -> dict:
    s = call("/lab/api/state")
    inc = s.get("incident") or {}
    row = {"step": label, "incident_state": s.get("incident_state"),
           "incident_sequence": s.get("incident_sequence"), "active": s.get("active"),
           "incident_id": inc.get("id") or None, "incident_status": inc.get("status"),
           "injected": [i.get("id") or i.get("bug") for i in (s.get("injected") or [])],
           "history_len": len(s.get("history") or [])}
    print(json.dumps(row), flush=True)
    return row


def approve_if_asked(snap: dict) -> dict | None:
    """P1 waits for a human. Run 1 of this driver did not approve, so both
    incidents sat at awaiting_approval until the next reset cancelled them and
    the 'closed' state was never reached. That run is kept as
    RESET_AND_STATE_run1_driver_defect.json."""
    inc = snap.get("incident") or {}
    ap = inc.get("approval") or {}
    if ap.get("required") and ap.get("decision") is None and ap.get("digest"):
        return call("/lab/api/approval", {"incident_id": inc.get("id"),
                                          "digest": ap["digest"], "decision": "approve"})
    return None


def wait_until(pred, limit=300.0, every=3.0) -> bool:
    end = time.time() + limit
    while time.time() < end:
        snap = call("/lab/api/state")
        approve_if_asked(snap)
        if pred(snap):
            return True
        time.sleep(every)
    return False


def main() -> None:
    rec: dict = {"generated_at": time.time(), "steps": []}

    # 1. Start from a known-clean lab.
    rec["reset_1"] = call("/lab/api/reset", {})
    time.sleep(2.0)
    rec["steps"].append(probe("after reset, before anything"))

    # 2. Run one incident to completion so there is a result to go stale.
    rec["inject_1"] = call("/lab/api/inject", {"bug": BUG})
    rec["steps"].append(probe("immediately after inject"))
    ok = wait_until(lambda s: not s.get("active")
                    and (s.get("incident") or {}).get("status") in ("resolved", "escalated", "failed"))
    rec["first_incident_finished"] = ok
    first = rec["steps"].append(probe("after the incident closed"))
    closed = rec["steps"][-1]

    # 3. The question finding 5 asked: does the closed result still look live?
    rec["closed_incident_still_served"] = closed["incident_id"] is not None
    rec["closed_incident_marked"] = closed["incident_state"]
    rec["closed_incident_status"] = closed["incident_status"]
    # Only meaningful if the incident actually finished.
    rec["closed_probe_is_valid"] = (rec["first_incident_finished"]
                                    and closed["incident_state"] == "closed")

    # 4. Reset must clear it.
    rec["reset_2"] = call("/lab/api/reset", {})
    time.sleep(2.0)
    rec["steps"].append(probe("after reset following a closed incident"))
    after_reset = rec["steps"][-1]
    rec["reset_clears_incident"] = after_reset["incident_id"] is None

    # 5. A fresh injection must not serve the previous incident while the new one opens.
    seq_before = after_reset["incident_sequence"]
    rec["inject_2"] = call("/lab/api/inject", {"bug": BUG})
    stale_reads = []
    end = time.time() + 30
    while time.time() < end:
        s = call("/lab/api/state")
        inc = s.get("incident") or {}
        approve_if_asked(s)
        if inc.get("id") and inc.get("status") in ("resolved", "escalated", "failed"):
            stale_reads.append({"t": round(time.time(), 1), "id": inc.get("id"),
                                "status": inc.get("status"),
                                "incident_state": s.get("incident_state")})
        if s.get("active"):
            break
        time.sleep(1.0)
    rec["stale_reads_during_new_incident"] = stale_reads
    rec["steps"].append(probe("while the second incident is opening"))
    wait_until(lambda s: not s.get("active")
               and (s.get("incident") or {}).get("status") in ("resolved", "escalated", "failed"))
    rec["steps"].append(probe("after the second incident closed"))
    rec["sequence_advanced"] = rec["steps"][-1]["incident_sequence"] > seq_before

    # 6. History and the JSONL evidence must survive a reset.
    rec["reset_3"] = call("/lab/api/reset", {})
    time.sleep(2.0)
    rec["steps"].append(probe("after the final reset"))
    rec["history_after_reset"] = rec["steps"][-1]["history_len"]
    try:
        with urllib.request.urlopen(BASE + "/lab/api/evidence", timeout=20) as r:
            lines = [l for l in r.read().decode().splitlines() if l.strip()]
        rec["evidence_records_after_reset"] = len(lines)
        rec["evidence_ids"] = [json.loads(l).get("id") for l in lines]
    except Exception as exc:
        rec["evidence_error"] = f"{exc.__class__.__name__}: {exc}"

    out = sys.argv[1] if len(sys.argv) > 1 else "reset_state.json"
    with open(out, "w") as fh:
        json.dump(rec, fh, indent=2)
    print(json.dumps({k: rec.get(k) for k in
                      ("closed_incident_still_served", "closed_incident_marked",
                       "reset_clears_incident", "stale_reads_during_new_incident",
                       "sequence_advanced", "history_after_reset",
                       "evidence_records_after_reset")}, indent=2), flush=True)
    print("wrote", out, flush=True)


if __name__ == "__main__":
    main()
