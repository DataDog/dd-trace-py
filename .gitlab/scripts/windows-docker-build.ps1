# Build a wheel inside the Windows Docker image.
# Usage: windows-docker-build.ps1 -VcArch <arch>
#   VcArch: "amd64" or "x64_x86" (for x86 cross-compile)
#
# Expects UV_PYTHON env var to be set (e.g. cpython-3.12-windows-x86_64)
param(
    [Parameter(Mandatory=$true)]
    [string]$VcArch
)
$ErrorActionPreference = 'Stop'

# vcvarsall.bat is at a fixed location - VS Build Tools 2022 is pre-installed in the image.
$vcvarsall = 'C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvarsall.bat'
if (-not (Test-Path $vcvarsall)) {
    Write-Error "vcvarsall.bat not found at $vcvarsall"
    exit 1
}

# Import MSVC environment into this PowerShell session by running vcvarsall in cmd
# and capturing the resulting environment variables.
Write-Host "=== Activating MSVC environment: $VcArch ==="
$cmdOut = cmd /c "`"$vcvarsall`" $VcArch && set" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Error "vcvarsall.bat failed with exit code $LASTEXITCODE"
    exit 1
}
foreach ($line in $cmdOut) {
    if ($line -match '^([^=]+)=(.+)$') {
        [System.Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], 'Process')
    }
}

# Required for setuptools/distutils MSVC detection on Python 3.12+
$env:DISTUTILS_USE_SDK = '1'
$env:MSSdk = '1'

Write-Host "=== Building wheel ==="
& uv build --wheel --out-dir C:\workspace\dist C:\workspace
if ($LASTEXITCODE -ne 0) {
    Write-Error "uv build failed"
    exit 1
}

Write-Host "=== Stripping source files from wheel ==="
Get-ChildItem C:\workspace\dist\*.whl | ForEach-Object {
    & uv run --no-project C:\workspace\scripts\zip_filter.py $_.FullName *.c *.cpp *.cc *.h *.hpp *.pyx *.md
    if ($LASTEXITCODE -ne 0) {
        Write-Error "zip_filter.py failed for $($_.Name)"
        exit 1
    }
}

Write-Host "=== Build complete ==="
Get-ChildItem C:\workspace\dist\*.whl | ForEach-Object { Write-Host $_.FullName }
