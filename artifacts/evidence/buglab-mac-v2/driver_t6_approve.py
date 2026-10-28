"""T6 approve path, rerun on its own.

In the combined run the approve case sampled the incident 10 s after the decision
and then the reject case reset the lab, which cancelled INC-0014 before it could
finish ("cancelled | The store was reset by you."). The approval itself was sound —
recorded once, and a replay of the same digest was refused with 409 — but the
outcome was never observed. This driver waits for the incident to finish and reads
the result back from the evidence log, which is authoritative.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8790"
HEAD = {"X-Bug-Lab": "1", "Content-Type": "application/json"}
BUG = "truncated-data"


def call(path: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data,
                                 headers=HEAD if data else {}, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return {"_http_error": exc.code, "_detail": exc.read().decode()[:300]}


def products_status() -> int:
    try:
        with urllib.request.urlopen(BASE + "/shop/api/products", timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as exc:
        return exc.code


def evidence(incident_id: str) -> dict:
    with urllib.request.urlopen(BASE + "/lab/api/evidence", timeout=20) as r:
        for line in r.read().decode().splitlines():
            if line.strip():
                rec = json.loads(line)
                if rec.get("id") == incident_id:
                    return rec
    return {}


def main() -> None:
    rec: dict = {"path": "approve", "bug": BUG, "generated_at": time.time()}
    call("/lab/api/reset", {})
    time.sleep(2.0)
    rec["store_before_inject"] = products_status()
    call("/lab/api/inject", {"bug": BUG})

    inc = {}
    end = time.time() + 180
    while time.time() < end:
        time.sleep(2.0)
        snap = call("/lab/api/state")
        cur = snap.get("incident") or {}
        ap = cur.get("approval") or {}
        if ap.get("required") and ap.get("decision") is None and ap.get("digest"):
            inc = cur
            break
    if not inc:
        rec["error"] = "no approval appeared"
        print(json.dumps(rec, indent=2)); return

    digest = inc["approval"]["digest"]
    rec["incident"] = inc["id"]
    rec["digest"] = digest
    rec["store_while_awaiting_approval"] = products_status()
    rec["decision_response"] = call("/lab/api/approval",
                                    {"incident_id": inc["id"], "digest": digest,
                                     "decision": "approve"})
    rec["replay_of_the_same_approval"] = call("/lab/api/approval",
                                              {"incident_id": inc["id"], "digest": digest,
                                               "decision": "approve"})
    rec["wrong_digest_rejected"] = call("/lab/api/approval",
                                        {"incident_id": inc["id"], "digest": "0" * 64,
                                         "decision": "approve"})

    # Each attempt's patch gets its own digest-bound gate. Run 1 of this driver
    # approved once, the model's patch failed verification, a SECOND gate opened
    # for the next attempt, and the incident sat at awaiting_approval until the
    # wait ran out. Approve every gate that appears, recording each one.
    rec["gates"] = [{"digest": digest, "response": rec["decision_response"]}]
    end = time.time() + 420
    while time.time() < end:
        time.sleep(3.0)
        snap = call("/lab/api/state")
        cur = snap.get("incident") or {}
        ap = cur.get("approval") or {}
        if (ap.get("required") and ap.get("decision") is None and ap.get("digest")
                and ap["digest"] not in [g["digest"] for g in rec["gates"]]):
            out = call("/lab/api/approval", {"incident_id": cur["id"],
                                             "digest": ap["digest"], "decision": "approve"})
            rec["gates"].append({"digest": ap["digest"], "response": out})
        if not snap.get("active") and cur.get("status") in ("resolved", "escalated", "failed"):
            break
    rec["gate_count"] = len(rec["gates"])
    final = call("/lab/api/state").get("incident") or {}
    rec["final_status"] = final.get("status")
    rec["final_outcome"] = final.get("outcome")
    rec["store_after"] = products_status()
    log = evidence(rec["incident"])
    rec["evidence_status"] = log.get("status")
    rec["evidence_outcome"] = log.get("outcome")
    rec["evidence_approval_decision"] = (log.get("approval") or {}).get("decision")
    rec["evidence_patch_author"] = (log.get("patch") or {}).get("author")
    rec["evidence_attempts"] = [(a.get("author"), (a.get("verification") or {}).get("verdict"))
                                for a in (log.get("attempts") or [])]
    with open("artifacts/evidence/buglab-mac-v2/T6_approve_path.json", "w") as fh:
        json.dump(rec, fh, indent=2)
    print(json.dumps({k: rec.get(k) for k in
                      ("incident", "gate_count", "decision_response", "replay_of_the_same_approval",
                       "wrong_digest_rejected", "final_status", "final_outcome",
                       "store_while_awaiting_approval", "store_after",
                       "evidence_approval_decision", "evidence_patch_author",
                       "evidence_attempts")}, indent=2))


if __name__ == "__main__":
    main()
