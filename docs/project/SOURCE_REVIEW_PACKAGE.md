# Source Review Package

The source-review package is a portable snapshot of one Git commit. It is software
review evidence only: it does not certify a research gate, validate a model, or
authorize execution.

## Build

Run from a full-history Git checkout with Python 3.11+ and Git available. The named
`origin/main` must already be fetched locally and point to the expected commit. The
command does not fetch, access the network, write Git objects or refs, or change
tracked checkout files.

Both build and verification require the preserved upstream baseline
`bf9bd197c9f4a05ae55ade254802a9eef1a74356`, its ancestry and unchanged MIT
license. Shallow checkouts are rejected before package I/O, even when the
baseline object is available. Use a normal full-history clone, or explicitly
fetch the missing history before packaging; the utility does not fetch it.

PowerShell:

```powershell
$sourceSha = (git rev-parse HEAD).Trim()
python -m scripts.package_source_review `
  --expected-source-sha $sourceSha `
  --output-dir ".audit/source-review-$($sourceSha.Substring(0, 12))"
```

Linux or macOS (Bash):

```bash
source_sha="$(git rev-parse HEAD)"
python -m scripts.package_source_review \
  --expected-source-sha "$source_sha" \
  --output-dir ".audit/source-review-${source_sha:0:12}"
```

The output directory must be new and cannot be inside `artifacts/` or Git metadata.
The command refuses malformed or mismatched source SHAs, a dirty tracked checkout,
unsafe paths, missing required review assets, and unresolved current-facing links.
It leaves partial output in place if a later build or verification step fails.

## Contents

The ZIP contains every regular tracked file at the pinned commit, preserving each
file's raw checkout bytes and Git mode, plus
`review/SUBMISSION_MANIFEST.json` and `review/SUBMISSION_SUMMARY.md`. It omits
untracked and ignored files, including local caches, credentials, and model weights;
it does not import models or run training.

The manifest's `generated_at` field and each ZIP member timestamp use the source
commit's UTC committer time. Wall-clock time and file mtimes do not change the archive
hash when checkout bytes and Python and zlib versions stay the same. ZIP members still
contain checkout bytes, so different Git line-ending or clean-filter policies across
clones can change the archive hash. Different Python or zlib versions can also change
the compressed bytes. Verification reconstructs the canonical ZIP, so use the
producer's Python and zlib versions and matching checkout conversions. A
different compression toolchain is not an accepted substitute for byte identity.

`source_inventory.json` records each Git path, mode, blob ID, raw-byte SHA-256, and
size. The blob ID is the Git clean-filter identity; it can differ from the raw-byte
SHA-256 when checkout conversion such as CRLF applies. `proof.json` records the
source commit and tree, package hash, archive/member checks, extracted-link result,
and selected-inventory hash. The upstream ancestry and byte-identical MIT license
are required rather than optional. Verification
rebuilds the manifest and summary from the pinned source. It checks each declared
ZIP member size and their total before CRC validation. It extracts files into the
system temporary directory, outside the package output and Git metadata. Both
temporary work directories use the same resolved scratch root; verification stops
before scratch creation if no usable root outside those protected paths is available.

Fixed-schema sidecars use bounded reads before parsing. `proof.json` is limited
to 64 KiB; the source inventory, selected manifest and summary are each limited
to 4 MiB. These limits also bound a file that grows during the read.
After ZIP member-size checks and before CRC validation, verification rebuilds
the archive with the existing writer and compares its complete hash and size.
Rehashed preambles, suffixes and noncanonical ZIP metadata are rejected.

Proof status is `NOT_CERTIFIED`; `empirical_metrics` is `null`, and execution remains
unauthorized. Review readiness is a packaging result, not a scientific conclusion.
To verify an existing package, keep `HEAD` at its recorded source commit. Pass the
same `--expected-source-sha` and `--output-dir` arguments with `--verify`. Tracked
files and the index must be clean. The fetched `origin/main` may have advanced, but
its history must contain the recorded source commit. Builds still require `HEAD` and
`origin/main` to match the expected source SHA exactly.
