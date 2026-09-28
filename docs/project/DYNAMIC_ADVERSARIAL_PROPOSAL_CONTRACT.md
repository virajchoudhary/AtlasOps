# Prospective dynamic adversarial scenario proposals

**Status: GENERATED_UNAPPROVED / NON_EMPIRICAL.** This is a bounded offline
proposal contract, not a dynamic benchmark result or permission to apply a
Chaos Mesh manifest. It does not change the 28 frozen G5 scenarios, their
splits or hashes, or the mock-only `bench.runner`.

## Generation boundary

`agents.adversarial_designer.design_scenario` requires an existing absolute
output directory outside the checkout. Importing the module creates nothing.
Callers must deliberately invoke the judge; there is no default output path,
automatic benchmark hook, cluster client, or fault application. The module
disables ambient HTTP proxies and caps the judge's HTTP response at 256 KiB
and the extracted JSON at 64 KiB. Missing or malformed model output fails
without a fabricated fallback scenario.

The judge response must be exactly one JSON object with a safe `adv-` ID,
bounded title and causal descriptions, difficulty, at least two root-cause
links, at least one red herring, and 2-4 faults. Faults must use the enumerated
kind/action pairs and Online Boutique service allowlist. Numeric, timing,
duration, target, peer, container, and parameter sets are bounded and
validated. YAML is emitted from structured dictionaries through
`yaml.safe_dump_all`; model text is never interpolated into YAML or a path.
The selector is limited to an allowed service in `default`, with Chaos Mesh
resources in `chaos-mesh` and one target pod per fault. Output names cannot
traverse directories or overwrite existing IDs. Batches accept only 1-10
proposals. Symlink/junction output directories and destinations inside the
checkout are refused.

Each proposal has a YAML file and JSON sidecar with the validated source
fields, SHA-256 of the response, prompt and manifest, requested judge-model
name, generation time, and explicit `GENERATED_UNAPPROVED`,
`empirical_claim_allowed=false`, `execution_authorized=false`. A requested
mutable model name is **not** an attested model digest. The raw model response
is not persisted. Temporary files are written in the output directory and
linked to new destination names without replacing an existing proposal.
The two-file publication is not an atomic transaction: a crash or second
link failure may leave an orphan metadata sidecar, which must not be treated
as an executable or completed proposal. Operators must serialize changes to
the output parent; a hostile concurrent same-user directory replacement is
outside this portable filesystem guarantee.

## Acceptance still missing

The local tests validate the advertised primitive shapes, parser failures,
YAML-injection resistance, path confinement, bounded output, duplicate IDs,
and proposal hashes with a mocked judge. They do not prove that every
manifest is admitted by the target Chaos Mesh CRDs, that an alleged red
herring is causally unrelated, that the generated case is novel, or that the
model identity and failure history are authentic. A separately approved
protocol must freeze membership, seed, model identity, source SHA, review
criteria, safe cluster context, rollback, and raw evidence before any
adversarial run can contribute to G13. No judge call, model download,
cluster mutation, P1 approval, or experiment is performed by this change.
