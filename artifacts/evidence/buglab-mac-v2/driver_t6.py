"""T6 (v2) — the three approval outcomes: approved, rejected, and expired.

Reads everything back from the API; nothing is assumed.

Two corrections to the v1 driver. /lab/api/reset is a POST that requires the
X-Bug-Lab header, and call() only sends the header when there is a payload, so
the bare call("/lab/api/reset") in v1 went out as an unauthenticated GET and did
nothing; every reset here passes {}. The approved path was previously covered
only incidentally by T7 and is now explicit.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8790"
HEAD = {"X-Bug-Lab": "1", "Content-Type": "application/json"}
P1_BUG = "truncated-data"  # server-detected, so it needs no browser


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
    """The store's own API: 500 while the bug is present, 200 once it is fixed."""
    try:
        with urllib.request.urlopen(BASE + "/shop/api/products", timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as exc:
        return exc.code


def wait_for_approval(deadline_s: float = 90.0) -> dict:
    end = time.time() + deadline_s
    while time.time() < end:
        inc = state().get("incident") or {}
        ap = inc.get("approval") or {}
        if ap.get("required") and ap.get("decision") is None and ap.get("digest"):
            return inc
        time.sleep(1.5)
    return {}


def path_approve() -> dict:
    call("/lab/api/reset", {})
    time.sleep(1.0)
    call("/lab/api/inject", {"bug": P1_BUG})
    inc = wait_for_approval()
    if not inc:
        return {"path": "approve", "error": "no approval appeared"}
    before = products_status()
    digest = inc["approval"]["digest"]
    out = call("/lab/api/approval", {"incident_id": inc["id"], "digest": digest,
                                     "decision": "approve"})
    time.sleep(10.0)
    snap = state()
    final = snap.get("incident") or {}
    # The same approval must not be usable twice.
    replay = call("/lab/api/approval", {"incident_id": inc["id"], "digest": digest,
                                        "decision": "approve"})
    return {
        "path": "approve",
        "incident": final.get("id"),
        "decision_response": out,
        "replay_of_the_same_approval": replay,
        "store_status_before_decision": before,
        "store_status_after_decision": products_status(),
        "status": final.get("status"),
        "outcome": final.get("outcome"),
        "patch_author": (final.get("patch") or {}).get("author"),
        "patch_kind": (final.get("patch") or {}).get("kind"),
        "approval": final.get("approval"),
        "still_injected": [b.get("id") for b in (snap.get("injected") or [])],
    }


def path_reject() -> dict:
    call("/lab/api/reset", {})
    time.sleep(1.0)
    call("/lab/api/inject", {"bug": P1_BUG})
    inc = wait_for_approval()
    if not inc:
        return {"path": "reject", "error": "no approval appeared"}
    before = products_status()
    out = call("/lab/api/approval", {"incident_id": inc["id"],
                                     "digest": inc["approval"]["digest"],
                                     "decision": "reject"})
    time.sleep(8.0)
    snap = state()
    final = snap.get("incident") or {}
    return {
        "path": "reject",
        "incident": final.get("id"),
        "decision_response": out,
        "store_status_before_decision": before,
        "store_status_after_decision": products_status(),
        "status": final.get("status"),
        "outcome": final.get("outcome"),
        "approval": final.get("approval"),
        "still_injected": [b.get("id") for b in (snap.get("injected") or [])],
    }


def path_expire(window_s: float = 330.0) -> dict:
    call("/lab/api/reset", {})
    time.sleep(1.0)
    call("/lab/api/inject", {"bug": P1_BUG})
    inc = wait_for_approval()
    if not inc:
        return {"path": "expire", "error": "no approval appeared"}
    expires_in = (inc.get("approval") or {}).get("expires_in")
    started = time.time()
    seen = []
    while time.time() - started < window_s:
        time.sleep(20.0)
        snap = state()
        cur = snap.get("incident") or {}
        ap = cur.get("approval") or {}
        seen.append({"t": round(time.time() - started), "status": cur.get("status"),
                     "stage": cur.get("stage"), "decision": ap.get("decision"),
                     "expires_in": ap.get("expires_in")})
        if cur.get("status") not in {"awaiting_approval"}:
            break
    snap = state()
    final = snap.get("incident") or {}
    return {
        "path": "expire",
        "incident": final.get("id"),
        "expires_in_at_start": expires_in,
        "observed": seen,
        "status": final.get("status"),
        "outcome": final.get("outcome"),
        "approval": final.get("approval"),
        "store_status": products_status(),
        "still_injected": [b.get("id") for b in (snap.get("injected") or [])],
    }


if __name__ == "__main__":
    results = {"bug_used": P1_BUG, "generated_at": time.time()}
    print("--- approve path", flush=True)
    results["approve"] = path_approve()
    print(json.dumps(results["approve"], indent=2)[:900], flush=True)
    print("--- reject path", flush=True)
    results["reject"] = path_reject()
    print(json.dumps(results["reject"], indent=2)[:900], flush=True)
    print("--- expire path (about 5.5 minutes)", flush=True)
    results["expire"] = path_expire()
    print(json.dumps(results["expire"], indent=2)[:1200], flush=True)
    out = "artifacts/evidence/buglab-mac-v2/T6_approval_paths.json"
    with open(out, "w") as fh:
        json.dump(results, fh, indent=2)
    print("wrote", out, flush=True)
