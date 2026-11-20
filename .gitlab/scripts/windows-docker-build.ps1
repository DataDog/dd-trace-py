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

# Keep Rust proc-macro DLLs in the disposable container filesystem. When these
# are built in the mounted checkout, Windows can retain a lock after the build
# and prevent GitLab Runner from cleaning the workspace for the next job.
$env:_DD_NATIVE_CARGO_TARGET_DIR = 'C:\cargo-target'

# rust-tuf has too long paths
Write-Host "=== Enabling git long paths ==="
git config --global core.longpaths true

Write-Host "=== Building wheel ==="
$distDir = 'C:\workspace\dist'
if (Test-Path $distDir) {
    Remove-Item -Recurse -Force $distDir
}
New-Item -ItemType Directory -Path $distDir | Out-Null

# Windows can briefly lock toolchain executables (for example link.exe),
# and package-index connections can reset. Retry once in the same container
# only for these transient failures, so real build defects are not masked
# (retries on main must stay infrastructure-only).
$transientBuildErrorPattern = 'LNK1104|being used by another process|os error 32\b|os error 10054|connection reset|forcibly closed by the remote host'
$maxBuildAttempts = 2
for ($attempt = 1; $attempt -le $maxBuildAttempts; $attempt++) {
    # Merge stderr in cmd: Windows PowerShell 5.1 with
    # $ErrorActionPreference = 'Stop' aborts on native stderr redirected by 2>&1.
    cmd /c "uv build --wheel --out-dir $distDir C:\workspace 2>&1" | Tee-Object -Variable buildOutput
    $buildExitCode = $LASTEXITCODE
    if ($buildExitCode -eq 0) {
        break
    }

    $transientMatch = $buildOutput | Select-String -Pattern $transientBuildErrorPattern | Select-Object -First 1
    if ($attempt -eq $maxBuildAttempts -or -not $transientMatch) {
        Write-Error "uv build failed with exit code $buildExitCode on attempt $attempt" -ErrorAction Continue
        exit $buildExitCode
    }

    Write-Warning "uv build attempt $attempt failed with a transient error ($($transientMatch.Line.Trim())); retrying in 5 seconds"
    Start-Sleep -Seconds 5
    Remove-Item -Recurse -Force $distDir
    New-Item -ItemType Directory -Path $distDir | Out-Null
}

$wheels = @(Get-ChildItem "$distDir\*.whl")
# Diagnostics: other jobs' wheels sometimes appear here during the build.
Write-Host "=== Wheels in $distDir ==="
$wheels | ForEach-Object {
    Write-Host ("{0}  {1:o}  {2} bytes" -f $_.Name, $_.LastWriteTimeUtc, $_.Length)
}
if ($wheels.Count -ne 1) {
    $names = ($wheels | ForEach-Object { $_.Name }) -join ', '
    Write-Error "Expected exactly one wheel in $distDir, found $($wheels.Count): $names"
    exit 1
}

Write-Host "=== Stripping source files from wheel ==="
$wheels | ForEach-Object {
    & uv run --no-project C:\workspace\scripts\zip_filter.py $_.FullName *.c *.cpp *.cc *.h *.hpp *.pyx *.md
    if ($LASTEXITCODE -ne 0) {
        Write-Error "zip_filter.py failed for $($_.Name)"
        exit 1
    }
}

Write-Host "=== Build complete ==="
$wheels | ForEach-Object { Write-Host $_.FullName }
