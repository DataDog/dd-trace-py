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
    Write-Host "No local win_amd64 wheel found for tag '${wheelTag}'."
    Write-Host "Downloading wheels from the last successful main pipeline..."

    # Download artifacts from the last successful 'build windows' job on main
    $pyVer = $env:PYTHON_VERSION
    $jobName = "build windows: [$pyVer, amd64]"
    $apiUrl = "$env:CI_API_V4_URL/projects/$env:CI_PROJECT_ID/jobs/artifacts/main/download?job=$([uri]::EscapeDataString($jobName))"
    $zipPath = "$env:CI_PROJECT_DIR\pywheels\wheels.zip"
    New-Item -ItemType Directory -Path "$env:CI_PROJECT_DIR\pywheels" -Force | Out-Null

    Write-Host "Fetching from: $apiUrl"
    try {
        Invoke-WebRequest -Uri $apiUrl -Headers @{ "JOB-TOKEN" = $env:CI_JOB_TOKEN } -OutFile $zipPath
        Expand-Archive -Path $zipPath -DestinationPath "$env:CI_PROJECT_DIR\pywheels" -Force
        Remove-Item $zipPath -ErrorAction SilentlyContinue
        # Flatten: artifacts may be nested under pywheels/pywheels/
        Get-ChildItem "$env:CI_PROJECT_DIR\pywheels" -Recurse -Filter "*.whl" | ForEach-Object {
            if ($_.DirectoryName -ne "$env:CI_PROJECT_DIR\pywheels") {
                Move-Item $_.FullName "$env:CI_PROJECT_DIR\pywheels\" -Force
            }
        }
        $wheels = Get-Item "$env:CI_PROJECT_DIR\pywheels\ddtrace*${wheelTag}*win_amd64.whl" -ErrorAction SilentlyContinue
    } catch {
        Write-Host "Failed to download artifacts: $_"
    }
}
if (-not $wheels) {
    Write-Host "WARNING: No win_amd64 wheel available for tag '${wheelTag}'."
    Write-Host "Available files in pywheels/:"
    Get-ChildItem "$env:CI_PROJECT_DIR\pywheels\" -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "  $_" }
    Write-Host ""
    Write-Host "Skipping Windows tests: no compatible wheel found."
    exit 0
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
