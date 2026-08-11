# Reproducible development dependencies

`pyproject.toml` is the source of truth for AtlasOps runtime, development, and
training dependency intent. This directory adds a reproducible local-development
resolution without changing the project to a dependency-manager-specific workflow.

## Files

- `dev.in` is the small, human-maintained input. It installs AtlasOps editable with
  its `dev` extra plus the package-build tools used by validation.
- `dev-win-py312.lock` is the compiled Windows x64 / CPython 3.12 development
  resolution. It pins resolved third-party packages but keeps the project itself as
  the relative editable input `-e .[dev]`.

The development lock intentionally excludes `.[train]`, PyTorch, Transformers, TRL,
PEFT, model runtimes, model downloads, and GPU-specific packages. It is not a universal
Linux, ROCm, CUDA, GPU-training, or production-deployment lock. Python 3.11 and 3.12 CI
compatibility testing remains separate from this Windows local-development lock.

## Install

From the repository root in PowerShell:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip --isolated install --index-url https://pypi.org/simple -r requirements/dev-win-py312.lock
python -m pip check
```

`--isolated` prevents user and machine pip configuration from adding hidden indexes or
other resolver behavior. The lock itself deliberately contains no index URL, trusted
host, absolute repository path, or user-home path.

## Regenerate

The committed lock was generated with CPython 3.12.5 and `pip-tools==7.6.0`. From the
repository root in PowerShell:

```powershell
$lockEnv = Join-Path $env:TEMP ("atlasops-lockgen-" + (Get-Date -Format "yyyyMMddHHmmss"))
py -3.12 -m venv $lockEnv
& "$lockEnv\Scripts\python.exe" -m pip --isolated install --index-url https://pypi.org/simple "pip-tools==7.6.0"
$previousPipConfigFile = $env:PIP_CONFIG_FILE
$previousPipIndexUrl = $env:PIP_INDEX_URL
$previousPipExtraIndexUrl = $env:PIP_EXTRA_INDEX_URL
$previousPipTrustedHost = $env:PIP_TRUSTED_HOST
try {
    $env:PIP_CONFIG_FILE = "NUL"
    $env:PIP_INDEX_URL = $null
    $env:PIP_EXTRA_INDEX_URL = $null
    $env:PIP_TRUSTED_HOST = $null
    & "$lockEnv\Scripts\pip-compile.exe" --resolver=backtracking --index-url=https://pypi.org/simple --no-emit-index-url --no-emit-trusted-host --no-strip-extras --output-file=requirements/dev-win-py312.lock requirements/dev.in
} finally {
    $env:PIP_CONFIG_FILE = $previousPipConfigFile
    $env:PIP_INDEX_URL = $previousPipIndexUrl
    $env:PIP_EXTRA_INDEX_URL = $previousPipExtraIndexUrl
    $env:PIP_TRUSTED_HOST = $previousPipTrustedHost
    Remove-Item -LiteralPath $lockEnv -Recurse
}
```

Regenerate the lock whenever runtime or `dev` dependencies in `pyproject.toml`, the
build backend, or `requirements/dev.in` changes. Review the complete resulting diff,
confirm training packages remain excluded, and rerun the clean-environment and
clean-checkout validation before accepting it.
