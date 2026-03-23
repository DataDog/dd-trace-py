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

# If no local wheel, download from the last successful build on main
if (-not $wheels) {
    Write-Host "No local wheel found. Downloading from last successful main pipeline..."
    $jobName = "build windows: [$env:PYTHON_VERSION, amd64]"
    $apiUrl = "$env:CI_API_V4_URL/projects/$env:CI_PROJECT_ID/jobs/artifacts/main/download?job=$([uri]::EscapeDataString($jobName))"
    New-Item -ItemType Directory -Path "$env:CI_PROJECT_DIR\pywheels" -Force | Out-Null
    $zipPath = "$env:CI_PROJECT_DIR\pywheels\wheels.zip"
    Invoke-WebRequest -Uri $apiUrl -Headers @{ "JOB-TOKEN" = $env:CI_JOB_TOKEN } -OutFile $zipPath
    Expand-Archive -Path $zipPath -DestinationPath "$env:CI_PROJECT_DIR\pywheels" -Force
    Remove-Item $zipPath
    # Flatten nested pywheels/pywheels/ structure from artifact zip
    Get-ChildItem "$env:CI_PROJECT_DIR\pywheels" -Recurse -Filter "*.whl" | Move-Item -Destination "$env:CI_PROJECT_DIR\pywheels\" -Force -ErrorAction SilentlyContinue
    $wheels = Get-Item "$env:CI_PROJECT_DIR\pywheels\ddtrace*${wheelTag}*win_amd64.whl"
}

$wheel = $wheels[0].FullName
Write-Host "Using wheel: $wheel"
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
