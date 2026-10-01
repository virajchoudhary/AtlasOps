param(
    [switch]$Quiet
)

$ErrorActionPreference = "Stop"

$runner = Join-Path $PSScriptRoot "smoke_e2e_local.py"

if ($Quiet) {
    python $runner --quiet
} else {
    python $runner
}
exit $LASTEXITCODE
