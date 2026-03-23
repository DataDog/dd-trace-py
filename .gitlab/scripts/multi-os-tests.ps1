# Multi-OS test script for Windows
$ErrorActionPreference = "Stop"

# Install uv
Write-Host "Installing uv..."
powershell -Command "irm https://astral.sh/uv/install.ps1 | iex"
$env:Path = "$env:USERPROFILE\.local\bin;$env:Path"

# Create temp directory and install in isolation
$env:TMPDIR = [System.IO.Path]::GetTempPath() + [System.Guid]::NewGuid().ToString()
New-Item -ItemType Directory -Path $env:TMPDIR -Force | Out-Null
Set-Location $env:TMPDIR
Write-Host "Installing test dependencies and ddtrace in $env:TMPDIR..."
uv python install --force $env:PYTHON_VERSION
uv venv --python $env:PYTHON_VERSION .venv
$wheelTag = "cp$($env:PYTHON_VERSION -replace '\.','')"
$wheels = Get-Item "$env:CI_PROJECT_DIR\pywheels\ddtrace*${wheelTag}*win_amd64.whl" -ErrorAction SilentlyContinue
if (-not $wheels) {
    Write-Host "WARNING: No matching win_amd64 wheel found for tag '${wheelTag}' in pywheels/"
    Write-Host "Available files in pywheels/:"
    Get-ChildItem "$env:CI_PROJECT_DIR\pywheels\" -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "  $_" }
    Write-Host ""
    Write-Host "Skipping Windows tests: the 'build windows' job did not run in this pipeline."
    Write-Host "This is expected for pipelines that do not include native code changes."
    exit 0
}
$wheel = $wheels[0].FullName
uv pip install --python $env:PYTHON_VERSION `
  -r "$env:CI_PROJECT_DIR\.gitlab\requirements\multi-os-tests.txt" `
  $wheel

# Run tests
$env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
Set-Location $env:TMPDIR
& ".\\.venv\\Scripts\\Activate.ps1"
Write-Host "Running tests on Windows with Python $env:PYTHON_VERSION"
python -m pytest "$env:CI_PROJECT_DIR\tests\internal\service_name\test_extra_services_names.py" -v -s
python -m pytest "$env:CI_PROJECT_DIR\tests\appsec\architectures\test_appsec_loading_modules.py" -v -s
