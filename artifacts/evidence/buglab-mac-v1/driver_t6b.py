"""T6b — let a P1 approval expire without deciding, and record what the lab does.

The first attempt injected while the previous escalated incident was still closing,
so no approval ever appeared. This one waits for the lab to be idle first and
checks the inject response instead of assuming it worked.
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


def state() -> dict:
    return call("/lab/api/state")


def products_status() -> int:
    try:
        with urllib.request.urlopen(BASE + "/shop/api/products", timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as exc:
        return exc.code


def wait_idle(limit: float = 60.0) -> bool:
    end = time.time() + limit
    while time.time() < end:
        snap = state()
        inc = snap.get("incident") or {}
        if not snap.get("active") and inc.get("status") in (None, "closed", "resolved", "escalated"):
            return True
        time.sleep(2.0)
    return False


def main() -> None:
    record: dict = {"bug": BUG, "generated_at": time.time()}
    record["idle_before_start"] = wait_idle()
    call("/lab/api/reset")
    time.sleep(2.0)
    record["inject_response"] = call("/lab/api/inject", {"bug": BUG})

    inc = {}
    end = time.time() + 90
    while time.time() < end:
        time.sleep(2.0)
        cur = state().get("incident") or {}
        ap = cur.get("approval") or {}
        if ap.get("required") and ap.get("decision") is None and ap.get("digest"):
            inc = cur
            break
    if not inc:
        record["error"] = "no approval appeared"
        print(json.dumps(record, indent=2), flush=True)
        return

    ap = inc["approval"]
    record["incident"] = inc["id"]
    record["digest"] = ap["digest"]
    record["expires_in_at_start"] = ap.get("expires_in")
    record["store_status_while_pending"] = products_status()
    print(f"approval pending on {inc['id']}, expires_in={ap.get('expires_in')}s", flush=True)

    started = time.time()
    observed = []
    # Watch past the stated expiry, without ever deciding.
    while time.time() - started < (ap.get("expires_in") or 300) + 60:
        time.sleep(15.0)
        cur = state().get("incident") or {}
        cap = cur.get("approval") or {}
        observed.append({"t": round(time.time() - started), "status": cur.get("status"),
                         "stage": cur.get("stage"), "decision": cap.get("decision"),
                         "expires_in": cap.get("expires_in"), "outcome": cur.get("outcome")})
        print(f"  t={observed[-1]['t']}s status={observed[-1]['status']} "
              f"expires_in={observed[-1]['expires_in']}", flush=True)
        if cur.get("status") != "awaiting_approval":
            break

    snap = state()
    final = snap.get("incident") or {}
    record["observed"] = observed
    record["final_status"] = final.get("status")
    record["final_outcome"] = final.get("outcome")
    record["final_approval"] = final.get("approval")
    record["store_status_at_end"] = products_status()
    record["site_version_at_end"] = call("/lab/api/site-version")
    record["checks_failing_at_end"] = [c.get("name") for c in (snap.get("checks") or [])
                                       if c.get("ok") is False]
    out = ("/private/tmp/claude-501/-Users-dhruvaambhaikar/"
           "e5658cdf-65c1-41f0-8efa-5746786dd6a6/scratchpad/t6b_expire.json")
    with open(out, "w") as fh:
        json.dump(record, fh, indent=2)
    print(json.dumps({k: record[k] for k in
                      ("incident", "final_status", "final_outcome", "store_status_at_end")},
                     indent=2), flush=True)
    print("wrote", out, flush=True)


if __name__ == "__main__":
    main()
