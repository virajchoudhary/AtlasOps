# G4 v3.8 Transport Diagnostics v1

Prospective observability and loopback qualification amendment only. The
previous benign qualification remains exactly NOT_QUALIFIED, unscored, and
unretried. Its raw journal and archive are not modified.

Transport observations are separate from model output. Record the exact HTTP
status, RPC operation, existing numeric request ID, safe normalized content-type,
and elapsed time. Do not record HTTP response bodies, endpoint/tunnel URLs,
inference keys, credential headers, cookies, or operational secrets. Retain
existing failure categories, fail-closed controls, and bounded deadlines.
Record a header-time observation before consuming the response, and a final
elapsed observation when available. A timeout must not erase a received status.

## Qualification Order

1. Restore the exact 74-package inference runtime from the two immutable locks.
2. Verify actual T4, Base/tokenizer revision and file identities, and unchanged v17.
3. Start the existing v3.8 inference server locally. Run one
   `scripts.qualify_loopback_inference` benign Base qualification against
   `127.0.0.1`, with a fresh dedicated inference key and a unique journal.
4. Only after successful loopback qualification, start the authorized free
   transient Cloudflare tunnel and run one existing tunneled benign qualification.
5. Preserve both raw results and transport observations. Terminate server,
   tunnel, proxy, and dedicated key material after execution.

Loopback qualification stops only its local proxy on success, retaining the
same server/model for the tunneled check. On failure, the operator terminates
the inference server and records cleanup before stopping. This local path has
no tools, approvals, verifier calls, or G4 reservation.
Each standalone qualifier writes an append-only `.transport-final.json` link
after proxy close/cleanup, including any close transport observations. The
earlier `.qualification.json` links its recorded prefix and is not rewritten.

Loopback failure gates the experiment as an inference-server/model-path
blocker. Tunneled failure after successful loopback gates it as a transport
blocker with exact sanitized status where observed. Do not change providers,
packages, hardware, decoding or budgets, and do not replay failed requests.
Only if both qualify may the already-authorized attempt 016 run once.

The scientific G4 predicate, scenario, model weights, decoding, load/inference
timeouts, training, and Validation population are unchanged. Any prospective
source-binding labels only bind the changed diagnostic source, preserving the
original v3.8 declaration and its historical evidence.
