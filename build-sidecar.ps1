param(
    [string]$AppRepoPath = "..\klin-app",
    [string]$PythonVersion = "3.13",
    [string]$TargetTriple = "",
    [switch]$SkipTauriCopy
)

$ErrorActionPreference = "Stop"
$isWindowsOs = $env:OS -eq "Windows_NT"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $projectRoot

try {
    $specPath = Join-Path $projectRoot "klin-worker.spec"

    $venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
    $venvPythonUnix = Join-Path $projectRoot ".venv/bin/python"

    if ($isWindowsOs -and (Test-Path $venvPython)) {
        & $venvPython -m PyInstaller --noconfirm $specPath
    } elseif (-not $isWindowsOs -and (Test-Path $venvPythonUnix)) {
        & $venvPythonUnix -m PyInstaller --noconfirm $specPath
    } elseif ($isWindowsOs) {
        py -$PythonVersion -m PyInstaller --noconfirm $specPath
    } else {
        python -m PyInstaller --noconfirm $specPath
    }

    $binaryName = if ($isWindowsOs) { "klin-worker.exe" } else { "klin-worker" }
    $binaryPath = Join-Path $projectRoot (Join-Path "dist" $binaryName)
    if (-not (Test-Path $binaryPath)) {
        throw "PyInstaller did not produce expected binary at $binaryPath"
    }

    if ($SkipTauriCopy) {
        Write-Host "Built sidecar binary at $binaryPath"
        return
    }

    if ([string]::IsNullOrWhiteSpace($TargetTriple)) {
        $targetTriple = (rustc --print host-tuple).Trim()
    } else {
        $targetTriple = $TargetTriple.Trim()
    }

    if (-not $targetTriple) {
        throw "Could not resolve host target triple via rustc --print host-tuple"
    }

    $targetDir = Join-Path $projectRoot (Join-Path $AppRepoPath (Join-Path "src-tauri" "binaries"))
    New-Item -ItemType Directory -Path $targetDir -Force | Out-Null

    $targetName = if ($isWindowsOs) {
        "klin-worker-$targetTriple.exe"
    } else {
        "klin-worker-$targetTriple"
    }

    $destPath = Join-Path $targetDir $targetName
    Copy-Item -Path $binaryPath -Destination $destPath -Force
    Write-Host "Sidecar copied to $destPath"
}
finally {
    Pop-Location
}
