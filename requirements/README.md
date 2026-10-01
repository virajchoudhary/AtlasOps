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
`.[dev]` includes the read-only Gradio demo. A headless runtime install uses
the base project requirements without Gradio; `.[demo]` adds it explicitly.

The hardcoded historical-chart generator is retired. Matplotlib and its orphaned
plotting dependencies are no longer included in `dev`; existing chart files
remain preserved as historical claim illustrations, not new experiment evidence.

## Install

From the repository root in PowerShell:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip --isolated install --index-url https://pypi.org/simple -r requirements/dev-win-py312.lock
python -m pip check
```

`--isolated` ignores user configuration, but does not disable machine configuration.
On Windows, set `PIP_CONFIG_FILE` to the exact lowercase value `nul` for the install
process when machine configuration adds an extra index. For example:

```powershell
python -c "import os; os.environ['PIP_CONFIG_FILE']=os.devnull; from pip._internal.cli.main import main; raise SystemExit(main(['--isolated', 'install', '--index-url', 'https://pypi.org/simple', '-r', 'requirements/dev-win-py312.lock']))"
```

This changes only the child process, not any persistent pip configuration. The lock
contains no index URL, trusted host, absolute repository path, or user-home path.

## Regenerate

The committed lock was generated with CPython 3.12.5, `pip-tools==7.6.0`, and
`pip==25.3`. The latter is a lock-tool pin, not a project runtime dependency:
pip-tools 7.6.0 is incompatible with the newer installed pip API. From the
repository root in PowerShell:

```powershell
$lockEnv = Join-Path $env:TEMP ("atlasops-lockgen-" + (Get-Date -Format "yyyyMMddHHmmss"))
py -3.12 -m venv $lockEnv
$previousPipConfigFile = $env:PIP_CONFIG_FILE
$previousPipIndexUrl = $env:PIP_INDEX_URL
$previousPipExtraIndexUrl = $env:PIP_EXTRA_INDEX_URL
$previousPipTrustedHost = $env:PIP_TRUSTED_HOST
try {
    $env:PIP_CONFIG_FILE = "nul"
    $env:PIP_INDEX_URL = $null
    $env:PIP_EXTRA_INDEX_URL = $null
    $env:PIP_TRUSTED_HOST = $null
    & "$lockEnv\Scripts\python.exe" -m pip --isolated install --index-url https://pypi.org/simple "pip==25.3" "pip-tools==7.6.0" "hatchling==1.32.0"
    & "$lockEnv\Scripts\pip-compile.exe" --resolver=backtracking --newline=lf --no-build-isolation --index-url=https://pypi.org/simple --no-emit-index-url --no-emit-trusted-host --no-strip-extras --pip-args="--retries 0 --timeout 20" --output-file=requirements/dev-win-py312.lock requirements/dev.in
} finally {
    $env:PIP_CONFIG_FILE = $previousPipConfigFile
    $env:PIP_INDEX_URL = $previousPipIndexUrl
    $env:PIP_EXTRA_INDEX_URL = $previousPipExtraIndexUrl
    $env:PIP_TRUSTED_HOST = $previousPipTrustedHost
    $resolved = [System.IO.Path]::GetFullPath($lockEnv)
    $tempRoot = [System.IO.Path]::GetFullPath($env:TEMP).TrimEnd('\') + '\'
    if (-not $resolved.StartsWith($tempRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Lock environment is outside the temporary directory."
    }
    Remove-Item -LiteralPath $resolved -Recurse
}
```

Regenerate the lock whenever runtime or `dev` dependencies in `pyproject.toml`, the
build backend, or `requirements/dev.in` changes. Review the complete resulting diff,
confirm training packages remain excluded, and rerun the clean-environment and
clean-checkout validation before accepting it.
