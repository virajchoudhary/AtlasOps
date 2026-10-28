# Bug Lab Local Runtime

The standalone Bug Lab can run in rules mode with Python 3.11 or 3.12 and no model,
training packages, cloud account, or container engine. Its native dependency graph is
in `requirements/buglab-runtime.lock`; it installs only the FastAPI application
runtime and its transitive dependencies.

The lock applies to native source installs and CI. `Dockerfile.buglab` is intentionally
unchanged and still pins only FastAPI, Uvicorn, and httpx directly. The Docker commands
below override the image command to select rules mode; they do not make its transitive
image dependencies hash-locked.

## Windows

From the repository root, create a virtual environment and install the hash-locked
runtime. Use `3.11` instead of `3.12` in the launcher command when needed.

```powershell
$venv = Join-Path $env:LOCALAPPDATA "AtlasOps\BugLab\venv"
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $venv) | Out-Null
py -3.12 -m venv $venv
$previousPipConfigFile = $env:PIP_CONFIG_FILE
try {
    $env:PIP_CONFIG_FILE = "nul"
    & "$venv\Scripts\python.exe" -m pip --isolated install --require-hashes --index-url https://pypi.org/simple -r requirements/buglab-runtime.lock
} finally {
    $env:PIP_CONFIG_FILE = $previousPipConfigFile
}
& "$venv\Scripts\python.exe" -m pip --isolated check
```

Choose a new or empty work directory for the first run. The server binds to loopback
by default.

```powershell
$venv = Join-Path $env:LOCALAPPDATA "AtlasOps\BugLab\venv"
& "$venv\Scripts\python.exe" -m demo.bug_lab --agents rules --workdir .\bug-lab-work
```

Stop with `Ctrl+C`. Start the same workspace again with `--resume` to keep its Store,
history, and evidence:

```powershell
$venv = Join-Path $env:LOCALAPPDATA "AtlasOps\BugLab\venv"
& "$venv\Scripts\python.exe" -m demo.bug_lab --agents rules --workdir .\bug-lab-work --resume
```

For a model-free Windows smoke test, install the test runner into that environment.
The test owns its loopback server and disposable workspace, kills that process to
simulate a crash, resumes it, and rejects the old approval receipt.

```powershell
& "$venv\Scripts\python.exe" -m pip --isolated install --index-url https://pypi.org/simple pytest
& "$venv\Scripts\python.exe" -m pytest tests/test_bug_lab_process_restart.py -q
```

## macOS

From the repository root, use the system Python 3.11 or 3.12. This example uses 3.12.

```sh
venv="$HOME/.local/share/atlasops-buglab-venv"
mkdir -p "$HOME/.local/share"
python3.12 -m venv "$venv"
PIP_CONFIG_FILE=/dev/null "$venv/bin/python" -m pip --isolated install --require-hashes --index-url https://pypi.org/simple -r requirements/buglab-runtime.lock
"$venv/bin/python" -m pip --isolated check
```

Use a new or empty directory for the first run. The default address is
`127.0.0.1:8790`.

```sh
venv="$HOME/.local/share/atlasops-buglab-venv"
"$venv/bin/python" -m demo.bug_lab --agents rules --workdir "$HOME/.local/share/atlasops-bug-lab"
```

Stop with `Ctrl+C`. Resume the same workspace with:

```sh
venv="$HOME/.local/share/atlasops-buglab-venv"
"$venv/bin/python" -m demo.bug_lab --agents rules --workdir "$HOME/.local/share/atlasops-bug-lab" --resume
```

The application readiness URL is `http://127.0.0.1:8790/health`. Readiness requires
the owned workspace and monitor to be available. It does not certify Store
resolution or a model endpoint. A broken Store can still have a ready application.

An unfinished incident becomes `cancelled` on graceful shutdown or `interrupted`
after crash recovery. Resume preserves the prior evidence without replaying
approvals or repairs. If the Store is still broken, a new incident requires a new
exact-change approval.

## Authenticated local proxy

Keep the existing model and proxy settings outside the repository. The
`scripts.local_llm_proxy:app` entrypoint remains compatible with the Mac deployment.
Missing, empty, or whitespace-only `PROXY_KEY` values return 503 without forwarding.
An incorrect key returns 401.

The proxy limits request bodies to 8 MiB and does not forward client Authorization
to Ollama. Upstream timeouts return 504, transport failures return 502, and upstream
error statuses retain their code with a generic response body. No upstream error
body or credential is included in those responses.

## Optional Docker Smoke

Prerequisites: a Docker Engine that is already running, and an unused local port
`8790`. No model is needed. The host-side port is bound to loopback only.

```sh
docker build --file Dockerfile.buglab --tag atlasops-buglab:local .
docker volume create atlasops-buglab-data
docker run --detach --name atlasops-buglab \
  --publish 127.0.0.1:8790:8790 \
  --mount type=volume,source=atlasops-buglab-data,target=/work \
  --entrypoint python atlasops-buglab:local \
  -m demo.bug_lab --host 0.0.0.0 --allow-remote \
  --workdir /work --agents rules
curl --fail --show-error http://127.0.0.1:8790/health
```

To stop and later resume the named-volume workspace, remove only the container and
keep the volume:

```sh
docker stop atlasops-buglab
docker rm atlasops-buglab
docker run --detach --name atlasops-buglab \
  --publish 127.0.0.1:8790:8790 \
  --mount type=volume,source=atlasops-buglab-data,target=/work \
  --entrypoint python atlasops-buglab:local \
  -m demo.bug_lab --host 0.0.0.0 --allow-remote \
  --workdir /work --agents rules --resume
```

This guide documents reproducible commands; it is not evidence of a physical Mac
execution or hardware qualification. The historical Mac runbook and its recorded
observations remain unchanged.

The Mac owner must separately verify the existing image and host model path,
foreground shutdown, Docker volume ownership and retention, explicit resume,
fresh browser telemetry, approval rejection and expiry, rollback, and evidence
download. CI verifies software contracts on hosted runners; it does not certify
the owner's physical Mac or authorize changes to that runtime.
