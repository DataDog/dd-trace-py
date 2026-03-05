# Build a Windows wheel for dd-trace-py using uv.
#
# Required environment variables:
#   PYTHON_VERSION   Python version to build for (e.g., "3.12")
#   WINDOWS_ARCH     Target architecture: "amd64" or "x86" (default: "amd64")
#
# Optional environment variables:
#   CI_PROJECT_DIR   Project root (defaults to two levels above this script)

$ErrorActionPreference = "Stop"

function section_start($id, $title) {
    $ts = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    Write-Host "`e[0Ksection_start:${ts}:${id}`r`e[0K${title}"
}
function section_end($id) {
    $ts = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    Write-Host "`e[0Ksection_end:${ts}:${id}`r`e[0K"
}

# ── Resolve config ─────────────────────────────────────────────────────────────
$PYTHON_VERSION = $env:PYTHON_VERSION
$WINDOWS_ARCH   = if ($env:WINDOWS_ARCH) { $env:WINDOWS_ARCH } else { "amd64" }
$CI_PROJECT_DIR = if ($env:CI_PROJECT_DIR) {
    [System.IO.Path]::GetFullPath($env:CI_PROJECT_DIR)
} else {
    (Resolve-Path "$PSScriptRoot\..\..")
}

if (-not $PYTHON_VERSION) { Write-Error "PYTHON_VERSION is required"; exit 1 }

# Map our arch name to uv's Python platform identifier
$UV_PYTHON_PLATFORM = if ($WINDOWS_ARCH -eq "x86") { "windows-x86" } else { "windows-x86_64" }
$env:UV_PYTHON = "cpython-${PYTHON_VERSION}-${UV_PYTHON_PLATFORM}"

# ── Setup directories ──────────────────────────────────────────────────────────
section_start "setup_env" "Setup environment"

$WORK_DIR = Join-Path ([System.IO.Path]::GetTempPath()) ([System.Guid]::NewGuid())
$BUILT_WHEEL_DIR = Join-Path $WORK_DIR "built_wheel"
$FINAL_WHEEL_DIR = Join-Path $CI_PROJECT_DIR "pywheels"
$DEBUG_WHEEL_DIR = Join-Path $CI_PROJECT_DIR "debugwheelhouse"

New-Item -ItemType Directory -Path $WORK_DIR, $BUILT_WHEEL_DIR, $FINAL_WHEEL_DIR, $DEBUG_WHEEL_DIR -Force | Out-Null

# Build performance — match .build_base in package.yml but lighter for Windows runners
$env:CMAKE_BUILD_PARALLEL_LEVEL = if ($env:CMAKE_BUILD_PARALLEL_LEVEL) { $env:CMAKE_BUILD_PARALLEL_LEVEL } else { "6" }
$env:CARGO_BUILD_JOBS           = if ($env:CARGO_BUILD_JOBS)           { $env:CARGO_BUILD_JOBS }           else { "6" }
$env:CMAKE_ARGS  = "-DNATIVE_TESTING=OFF"
# Required by ASM CMake to locate the Python3 library (mirrors Linux/macOS builds)
$env:CIBW_BUILD  = "1"

Write-Host "PYTHON_VERSION:    $PYTHON_VERSION"
Write-Host "WINDOWS_ARCH:      $WINDOWS_ARCH"
Write-Host "UV_PYTHON:         $env:UV_PYTHON"
Write-Host "CI_PROJECT_DIR:    $CI_PROJECT_DIR"

section_end "setup_env"

# ── Install uv ────────────────────────────────────────────────────────────────
section_start "install_uv" "Installing uv"
$env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    powershell -Command "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}
Write-Host "uv: $(uv --version)"
section_end "install_uv"

# ── Install 32-bit VC++ runtime (x86 only) ────────────────────────────────────
# 32-bit Python requires the x86 Visual C++ Redistributable.  The Windows
# runner ships only the 64-bit version, so the 32-bit Python process exits
# with 0xc0000135 (STATUS_DLL_NOT_FOUND) without this step.
if ($WINDOWS_ARCH -eq "x86") {
    section_start "install_vcredist_x86" "Installing 32-bit VC++ Redistributable"
    $vcRedist = Join-Path $env:TEMP "vc_redist.x86.exe"
    Invoke-WebRequest "https://aka.ms/vs/17/release/vc_redist.x86.exe" -OutFile $vcRedist
    & $vcRedist /install /quiet /norestart
    Remove-Item $vcRedist -Force -ErrorAction SilentlyContinue
    section_end "install_vcredist_x86"
}

# ── Install Python ─────────────────────────────────────────────────────────────
section_start "install_python" "Installing Python $PYTHON_VERSION ($WINDOWS_ARCH)"
# --force replaces any pre-existing shim that is not managed by uv
uv python install --force $env:UV_PYTHON
$PYTHON_EXE = (uv python find $env:UV_PYTHON).Trim()
Write-Host "Python executable: $PYTHON_EXE"
& $PYTHON_EXE --version
section_end "install_python"

# ── Setup MSVC ────────────────────────────────────────────────────────────────
# The runner has MSVC installed but the tools directory is not on PATH by
# default.  Use vswhere + vcvarsall to configure the build environment.
# For x86 we use the x64_x86 cross-compilation toolset (64-bit host → 32-bit target).
section_start "setup_msvc" "Setting up MSVC build environment"

$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
if (-not (Test-Path $vswhere)) {
    Write-Error "vswhere.exe not found — Visual Studio or Build Tools must be installed"
    exit 1
}

$vsPath = & $vswhere -latest -products * `
    -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 `
    -property installationPath 2>$null
if (-not $vsPath) {
    $vsPath = & $vswhere -latest -products * -property installationPath 2>$null
}
if (-not $vsPath) {
    Write-Error "No Visual Studio installation found"
    exit 1
}

$vcvarsall = Join-Path $vsPath "VC\Auxiliary\Build\vcvarsall.bat"
if (-not (Test-Path $vcvarsall)) {
    Write-Error "vcvarsall.bat not found at: $vcvarsall"
    exit 1
}

$vcArch = if ($WINDOWS_ARCH -eq "x86") { "x64_x86" } else { "amd64" }
Write-Host "Initializing MSVC: vcvarsall.bat $vcArch"

# Write a temp batch file that calls vcvarsall then dumps the environment.
# Using Set-Content avoids PowerShell 5.x here-string and shell-chain quoting issues.
$batchFile = Join-Path $env:TEMP ('setup_vcvars_' + $PID + '.bat')
Set-Content -Path $batchFile -Encoding ASCII -Value ('@call "' + $vcvarsall + '" ' + $vcArch + ' > NUL 2>&1')
Add-Content -Path $batchFile -Encoding ASCII -Value 'set'

& cmd.exe /c $batchFile | ForEach-Object {
    if ($_ -match '^([^=]+)=(.*)$') {
        Set-Item -Path "env:$($matches[1])" -Value $matches[2]
    }
}
Remove-Item $batchFile -Force -ErrorAction SilentlyContinue

$linkExe = Get-Command link.exe -ErrorAction SilentlyContinue
if (-not $linkExe) {
    Write-Error "link.exe not found after MSVC setup — check that Build Tools are installed"
    exit 1
}
Write-Host "link.exe: $($linkExe.Source)"
section_end "setup_msvc"

# ── Setup Rust ────────────────────────────────────────────────────────────────
section_start "setup_rust" "Setting up Rust"
$env:Path = "$env:USERPROFILE\.cargo\bin;$env:Path"
if (-not (Get-Command rustc -ErrorAction SilentlyContinue)) {
    $rustupInit = Join-Path $env:TEMP "rustup-init.exe"
    Invoke-WebRequest "https://win.rustup.rs/x86_64" -OutFile $rustupInit
    & $rustupInit -y --default-toolchain stable --no-modify-path
    $env:Path = "$env:USERPROFILE\.cargo\bin;$env:Path"
    Remove-Item $rustupInit -Force
}
rustup default stable
if ($WINDOWS_ARCH -eq "x86") {
    # Required for Rust to cross-compile to i686-pc-windows-msvc
    rustup target add i686-pc-windows-msvc
}
Write-Host "rustc: $(rustc --version)"
section_end "setup_rust"

# ── Build wheel ───────────────────────────────────────────────────────────────
section_start "build_wheel" "Building wheel (Python $PYTHON_VERSION, $WINDOWS_ARCH)"
Set-Location $CI_PROJECT_DIR

$PY_VER_NODOT = $PYTHON_VERSION -replace '\.', ''
$BUILD_LOG = Join-Path $DEBUG_WHEEL_DIR "build_cp${PY_VER_NODOT}_${WINDOWS_ARCH}.log"

# Tee output so we see it live and also capture it for the artifact.
# Temporarily relax $ErrorActionPreference: with "Stop" set, PowerShell treats
# every stderr line from a native command as a fatal NativeCommandError when
# piped through 2>&1.  We check $LASTEXITCODE explicitly instead.
$ErrorActionPreference = 'Continue'
uv build --wheel --out-dir $BUILT_WHEEL_DIR . 2>&1 | Tee-Object -FilePath $BUILD_LOG
$buildExitCode = $LASTEXITCODE
$ErrorActionPreference = 'Stop'
if ($buildExitCode -ne 0) {
    Write-Error "Build failed! Log: $BUILD_LOG"
    exit 1
}

$BUILT_WHEEL = (Get-ChildItem -Path $BUILT_WHEEL_DIR -Filter "*.whl" | Select-Object -First 1).FullName
if (-not $BUILT_WHEEL) { Write-Error "No .whl produced"; exit 1 }
Write-Host "Built: $BUILT_WHEEL"
section_end "build_wheel"

# ── Strip source files ────────────────────────────────────────────────────────
section_start "strip_wheel" "Stripping source files"
& $PYTHON_EXE "$CI_PROJECT_DIR\scripts\zip_filter.py" $BUILT_WHEEL "*.c" "*.cpp" "*.cc" "*.h" "*.hpp" "*.pyx" "*.md"
section_end "strip_wheel"

# ── Finalize ──────────────────────────────────────────────────────────────────
section_start "finalize_wheel" "Finalizing wheel"
$WHEEL_NAME  = [System.IO.Path]::GetFileName($BUILT_WHEEL)
$FINAL_WHEEL = Join-Path $FINAL_WHEEL_DIR $WHEEL_NAME
Copy-Item $BUILT_WHEEL -Destination $FINAL_WHEEL_DIR -Force
Write-Host "Final: $FINAL_WHEEL"
section_end "finalize_wheel"

# ── Validate RECORD ───────────────────────────────────────────────────────────
section_start "validate_wheel" "Validating wheel RECORD"
& $PYTHON_EXE "$CI_PROJECT_DIR\scripts\validate_wheel.py" $FINAL_WHEEL
section_end "validate_wheel"

# ── Smoke test ────────────────────────────────────────────────────────────────
section_start "smoke_test" "Running smoke test"
$TEST_DIR   = Join-Path $WORK_DIR "test_install"
$VENV_DIR   = Join-Path $TEST_DIR ".venv"
New-Item -ItemType Directory -Path $TEST_DIR | Out-Null

uv venv --python $PYTHON_EXE $VENV_DIR
$VENV_PYTHON = Join-Path $VENV_DIR "Scripts\python.exe"
uv pip install --python $VENV_PYTHON $FINAL_WHEEL
& $VENV_PYTHON "$CI_PROJECT_DIR\tests\smoke_test.py"
section_end "smoke_test"

Write-Host "Done: $FINAL_WHEEL"
