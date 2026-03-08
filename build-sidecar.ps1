param(
    [string]$AppRepoPath = "..\klin-app",
    [string]$PythonVersion = "3.13"
)

$ErrorActionPreference = "Stop"
$isWindowsOs = $env:OS -eq "Windows_NT"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $projectRoot

try {
    if ($isWindowsOs) {
        py -$PythonVersion -m PyInstaller --noconfirm --clean .\klin-worker.spec
    } else {
        python -m PyInstaller --noconfirm --clean .\klin-worker.spec
    }

    $binaryName = if ($isWindowsOs) { "klin-worker.exe" } else { "klin-worker" }
    $binaryPath = Join-Path $projectRoot "dist\$binaryName"
    if (-not (Test-Path $binaryPath)) {
        throw "PyInstaller did not produce expected binary at $binaryPath"
    }

    $targetTriple = (rustc --print host-tuple).Trim()
    if (-not $targetTriple) {
        throw "Could not resolve host target triple via rustc --print host-tuple"
    }

    $targetDir = Join-Path $projectRoot "$AppRepoPath\src-tauri\binaries"
    New-Item -ItemType Directory -Path $targetDir -Force | Out-Null

    $targetName = if ($isWindowsOs) {
        "klin-worker-$targetTriple.exe"
    } else {
        "klin-worker-$targetTriple"
    }

    Copy-Item -Path $binaryPath -Destination (Join-Path $targetDir $targetName) -Force
    Write-Host "Sidecar copied to $targetDir\$targetName"
}
finally {
    Pop-Location
}
