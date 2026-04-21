# Test a built wheel inside the Windows Docker image.
# Usage: windows-docker-test.ps1 -UvPython <spec>
#   UvPython: Python spec (e.g. cpython-3.12-windows-x86_64)
param(
    [Parameter(Mandatory=$true)]
    [string]$UvPython
)
$ErrorActionPreference = 'Stop'

Write-Host "=== Finding Python: $UvPython ==="
$pythonExe = & uv python find $UvPython
if (-not $pythonExe) {
    Write-Error "Could not find Python $UvPython"
    exit 1
}
Write-Host "Python: $pythonExe"

Write-Host "=== Creating venv ==="
& uv venv --python="$pythonExe" C:\testvenv
if ($LASTEXITCODE -ne 0) { Write-Error "uv venv failed"; exit 1 }

Write-Host "=== Installing wheel ==="
Get-ChildItem C:\workspace\dist\*.whl | ForEach-Object {
    & uv pip install --python C:\testvenv\Scripts\python.exe $_.FullName
    if ($LASTEXITCODE -ne 0) { Write-Error "wheel install failed"; exit 1 }
}

Write-Host "=== Running smoke test ==="
& C:\testvenv\Scripts\python.exe C:\workspace\tests\smoke_test.py
if ($LASTEXITCODE -ne 0) { Write-Error "smoke test failed"; exit 1 }

Write-Host "=== Smoke test passed ==="
