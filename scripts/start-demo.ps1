[CmdletBinding()]
param(
    [ValidateRange(1024, 65535)][int]$Port = 7864,
    [switch]$Check,
    [switch]$LiveObservations,
    [string]$IncidentCaptureRoot,
    [ValidatePattern('^EXP-STAGE4-SF002-\d{3}$')]
    [string]$IncidentId = 'EXP-STAGE4-SF002-018',
    [ValidateSet('golden', 'golden-v3', 'golden-v3b')]
    [string]$IncidentCapture = 'golden',
    [string]$OperatorConfig
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    $commonDir = & git -C $repo rev-parse --path-format=absolute --git-common-dir
    if ($LASTEXITCODE -eq 0) {
        $python = Join-Path (Split-Path -Parent $commonDir) '.venv\Scripts\python.exe'
    }
}
if (-not (Test-Path -LiteralPath $python)) {
    $python = (Get-Command python -ErrorAction Stop).Source
}
if ($IncidentCaptureRoot) {
    $capture = Get-Item -LiteralPath $IncidentCaptureRoot
    if (-not $capture.PSIsContainer -or $capture.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw 'Incident capture must be an existing directory, not a redirected path.'
    }
    $IncidentCaptureRoot = $capture.FullName
}
if ($OperatorConfig) {
    $OperatorConfig = (Get-Item -LiteralPath $OperatorConfig -ErrorAction Stop).FullName
}
$expectedMode = if ($OperatorConfig) { 'operator' } else { 'read-only' }

Push-Location -LiteralPath $repo
try {
    & $python -m demo.launcher --check
    if ($LASTEXITCODE -ne 0) { throw 'Presentation preflight failed.' }
    if ($Check) { return }

    function Get-DemoHealth {
        try {
            Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 3
        } catch { $null }
    }
    $health = Get-DemoHealth
    if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
        if ($health.mode -ne $expectedMode -or -not $health.frontend_built) {
            throw "Port $Port is occupied by an unverified server. Choose another -Port."
        }
        Write-Output "Existing presentation: http://127.0.0.1:$Port/#/demo"
        Write-Output 'Existing server options are unchanged.'
    } else {
        $logRoot = Join-Path $repo ('.codex-tmp\demo-start-' + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $logRoot | Out-Null
        $args = @('-m', 'demo.launcher', '--host', '127.0.0.1', '--port', "$Port")
        if ($LiveObservations) { $args += '--live-observations' }
        if ($OperatorConfig) { $args += @('--operator-config', ('"' + $OperatorConfig + '"')) }
        if ($IncidentCaptureRoot) {
            $args += @('--incident-capture-root', ('"' + $IncidentCaptureRoot + '"'),
                '--incident-id', $IncidentId, '--incident-capture', $IncidentCapture)
        }
        $server = Start-Process -FilePath $python -ArgumentList $args -WorkingDirectory $repo `
            -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logRoot 'server.stdout.log') `
            -RedirectStandardError (Join-Path $logRoot 'server.stderr.log')
        $deadline = [datetime]::UtcNow.AddSeconds(30)
        do {
            $health = Get-DemoHealth
            if ($health.mode -eq $expectedMode -and $health.frontend_built) { break }
            if ($server.HasExited) { throw "Presentation exited. Inspect $logRoot." }
            Start-Sleep -Milliseconds 250
        } while ([datetime]::UtcNow -lt $deadline)
        if ($health.mode -ne $expectedMode -or -not $health.frontend_built) {
            throw "Presentation startup is unverified. Inspect $logRoot before retrying."
        }
        Write-Output "Presentation: http://127.0.0.1:$Port/#/demo"
    }

    if ($LiveObservations) {
        if (-not $logRoot) {
            $logRoot = Join-Path $repo ('.codex-tmp\demo-start-' + [guid]::NewGuid().ToString('N'))
            New-Item -ItemType Directory -Path $logRoot | Out-Null
        }
        $kubectl = (Get-Command kubectl -ErrorAction Stop).Source
        $services = @(
            @{ Name = 'boutique'; Namespace = 'default'; Service = 'frontend'; Port = 17880; Target = 80 },
            @{ Name = 'coordinator'; Namespace = 'default'; Service = 'atlasops-coordinator-svc'; Port = 19099; Target = 9099 },
            @{ Name = 'prometheus'; Namespace = 'monitoring'; Service = 'prometheus-kube-prometheus-prometheus'; Port = 19090; Target = 9090 }
        )
        foreach ($service in $services) {
            if (Get-NetTCPConnection -LocalPort $service.Port -State Listen -ErrorAction SilentlyContinue) {
                Write-Output "$($service.Name): existing listener preserved"
                continue
            }
            $forwardArgs = @('--context', 'kind-atlasops-local', 'port-forward', '--address',
                '127.0.0.1', '-n', $service.Namespace, "service/$($service.Service)",
                "$($service.Port):$($service.Target)")
            $process = Start-Process -FilePath $kubectl -ArgumentList $forwardArgs -WindowStyle Hidden `
                -PassThru -RedirectStandardOutput (Join-Path $logRoot "$($service.Name).stdout.log") `
                -RedirectStandardError (Join-Path $logRoot "$($service.Name).stderr.log")
            Write-Output "$($service.Name): forward started, PID $($process.Id); response not yet verified"
        }
        Write-Output 'Refresh live observations on the website to check actual responses.'
    }
} finally {
    Pop-Location
}
