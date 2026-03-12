#!/usr/bin/env bash
# Build a Windows wheel for dd-trace-py using uv.
# Runs under Git Bash (bash.exe from Git for Windows) on windows-v2:2022 runners.
#
# Required env vars:
#   PYTHON_VERSION   Python version to build for (e.g. "3.12")
#   WINDOWS_ARCH     Target architecture: "amd64" or "x86" (default: "amd64")

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/build-wheel-helpers.sh"

# ── Windows config ────────────────────────────────────────────────────────────
PYTHON_VERSION=${PYTHON_VERSION:?PYTHON_VERSION is required}
WINDOWS_ARCH=${WINDOWS_ARCH:-amd64}

case "$WINDOWS_ARCH" in
    x86)   UV_PYTHON_PLATFORM="windows-x86";    VC_ARCH="x64_x86"   ;;
    amd64) UV_PYTHON_PLATFORM="windows-x86_64"; VC_ARCH="amd64"     ;;
    arm64) UV_PYTHON_PLATFORM="windows-x86_64"; VC_ARCH="x64_arm64" ;;
    *)     echo "ERROR: Unsupported WINDOWS_ARCH=$WINDOWS_ARCH" >&2; exit 1 ;;
esac
export UV_PYTHON="cpython-${PYTHON_VERSION}-${UV_PYTHON_PLATFORM}"

# ── Windows overrides ─────────────────────────────────────────────────────────

# Install the chosen Python via uv (not pre-installed as on manylinux/macOS).
setup_python() {
  section_start "setup_python" "Setting up Python ${UV_PYTHON} (${WINDOWS_ARCH})"
  export PATH="${UV_INSTALL_DIR:-$HOME/.local/bin}:${PATH}"
  if ! command -v uv &>/dev/null; then
    # Prefer PowerShell on Windows — it uses .NET WebClient and is unaffected
    # by any runner-level networking quirks that can hit MSYS2 curl.
    if command -v powershell.exe &>/dev/null; then
      powershell.exe -ExecutionPolicy ByPass -Command \
        "irm https://astral.sh/uv/install.ps1 | iex"
    else
      curl -LsSf https://astral.sh/uv/install.sh | sh
    fi
  fi
  uv python install --force "${UV_PYTHON}"
  # Export the concrete executable path for use in repair/test steps
  export PYTHON_EXE
  PYTHON_EXE=$(uv python find "${UV_PYTHON}")
  echo "Python: $PYTHON_EXE"
  "$PYTHON_EXE" --version

  echo "UV_DATA_DIR: ${UV_DATA_DIR}"
  echo "UV_CACHE_DIR: ${UV_CACHE_DIR}"
  echo "UV_PYTHON_INSTALL_DIR: ${UV_PYTHON_INSTALL_DIR}"
  echo "TEMP: ${TEMP}"
  echo "PYTHON_EXE: ${PYTHON_EXE}"

  # Verify Python.h exists
  local python_include_dir
  python_include_dir=$("${PYTHON_EXE}" -c "import sysconfig; print(sysconfig.get_path('include'))")
  echo "Python include dir: ${python_include_dir}"

  local python_include_unix
  python_include_unix=$(cygpath -u "${python_include_dir}" 2>/dev/null || echo "${python_include_dir}")
  if [[ -f "${python_include_unix}/Python.h" ]]; then
    echo "Python.h: FOUND"
  else
    echo "WARNING: Python.h NOT FOUND at ${python_include_dir}"
    ls -la "${python_include_unix}/" 2>/dev/null || echo "  (directory does not exist)"
  fi

  # Check for system32 in critical paths (would be invisible to 32-bit cl.exe)
  for var_name in UV_DATA_DIR UV_CACHE_DIR UV_PYTHON_INSTALL_DIR CARGO_HOME RUSTUP_HOME TEMP PYTHON_EXE; do
    local val="${!var_name}"
    if [[ "$val" == *"system32"* || "$val" == *"System32"* ]]; then
      echo "WARNING: ${var_name} contains 'system32' — may be invisible to 32-bit cl.exe"
    fi
  done

  section_end "setup_python"
}

# Extend Rust setup: add the i686 cross-compile target for win32 builds.
setup_rust() {
  section_start "install_rust" "Rust toolchain"
  export PATH="${CARGO_HOME:-$HOME/.cargo}/bin:${PATH}"
  if ! command -v rustc &>/dev/null; then
    local rustup_init="${WORK_DIR}/rustup-init.exe"
    if command -v powershell.exe &>/dev/null; then
      powershell.exe -ExecutionPolicy ByPass -Command \
        "Invoke-WebRequest -Uri 'https://win.rustup.rs/x86_64' -OutFile '$(cygpath -w "${rustup_init}")'"
    else
      curl --proto '=https' --tlsv1.2 -sSf -o "${rustup_init}" "https://win.rustup.rs/x86_64"
    fi
    "${rustup_init}" -y --no-modify-path
  fi
  rustup default stable
  if [[ "$WINDOWS_ARCH" == x86 ]]; then
    rustup target add i686-pc-windows-msvc
  fi
  if [[ "$WINDOWS_ARCH" == arm64 ]]; then
    rustup target add aarch64-pc-windows-msvc
  fi
  rustc --version
  section_end "install_rust"
}

# Windows has no auditwheel/delocate step.  Just strip source files and
# copy to TMP_WHEEL_DIR so finalize() (from the helpers) works unchanged.
repair_wheel() {
  section_start "strip_wheel" "Stripping source files"
  uv run --no-project scripts/zip_filter.py "${BUILT_WHEEL_FILE}" \*.c \*.cpp \*.cc \*.h \*.hpp \*.pyx \*.md
  section_end "strip_wheel"

  section_start "repair_wheel" "Finalizing wheel (no repair needed on Windows)"
  cp "${BUILT_WHEEL_FILE}" "${TMP_WHEEL_DIR}/"
  section_end "repair_wheel"
}

# On Windows, venv activation scripts live under Scripts/ not bin/.
test_wheel() {
  section_start "test_wheel" "Testing wheel"
  export UV_LINK_MODE=copy
  export TEST_WHEEL_DIR="${WORK_DIR}/test_wheel"
  mkdir -p "${TEST_WHEEL_DIR}"
  export VENV_PATH="${TEST_WHEEL_DIR}/venv"
  uv venv --python="${PYTHON_EXE}" "${VENV_PATH}"
  (
    unset UV_PYTHON
    uv pip install --python "${VENV_PATH}/Scripts/python.exe" "${FINAL_WHEEL_FILE}"
  )
  echo "=== Running smoke test ==="
  "${VENV_PATH}/Scripts/python.exe" "${PROJECT_DIR}/tests/smoke_test.py"
  section_end "test_wheel"
}

# ── Windows build_wheel override ──────────────────────────────────────────────
# Wraps `uv build` in a .bat file that activates the MSVC environment first.
# This scopes MSVC env to the build process only — not the bash session.
# Importing vcvarsall into bash corrupts Windows env vars (e.g. APPDATA) that
# uv and other tools depend on, causing cascading failures.
build_wheel() {
  section_start "build_wheel_function" "Building wheel"

  # Compute Python version/tag using the already-installed Python (no MSVC needed).
  PYTHON_VER=$(uv run --no-project python -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')")
  PYTHON_TAG=$(uv run --no-project python -c "
import sys
ver = f'{sys.version_info[0]}{sys.version_info[1]}'
plat = 'win_amd64' if sys.maxsize > 2**32 else 'win32'
print(f'cp{ver}-{plat}')
")
  export BUILD_LOG="${DEBUG_WHEEL_DIR}/build_${PYTHON_TAG}.log"
  echo "Building wheel for Python ${PYTHON_VER} (log: ${BUILD_LOG})"

  local build_bat build_bat_win vcvarsall_win built_wheel_dir_win project_dir_win python_exe_win
  local crt_stub_dir crt_stub_dir_win
  build_bat="${WORK_DIR}/build.bat"
  build_bat_win=$(cygpath -w "${build_bat}")
  vcvarsall_win=$(cygpath -w "${VCVARSALL_PATH}")
  built_wheel_dir_win=$(cygpath -w "${BUILT_WHEEL_DIR}")
  project_dir_win=$(cygpath -w "${PROJECT_DIR}")
  python_exe_win=$(cygpath -w "${PYTHON_EXE}")
  crt_stub_dir="${WORK_DIR}/arm64_crt_stubs"
  crt_stub_dir_win=$(cygpath -w "${crt_stub_dir}")

  # The batch file calls vcvarsall (activating MSVC env for this process only),
  # then runs uv build.  The MSVC env never leaks into the parent bash session.
  # Pass --python explicitly so uv uses the known-good installation path
  # rather than resolving via UV_PYTHON (which could find a stale install).
  #
  # DISTUTILS_USE_SDK=1 and MSSdk=1 tell setuptools/distutils to trust the
  # vcvarsall environment rather than doing its own MSVC registry search.
  # Required for Python 3.12+ where setuptools provides its own distutils
  # with stricter MSVC detection that fails without these flags.
  printf '@call "%s" %s\r\n' "${vcvarsall_win}" "${VCVARSALL_ARCH}" > "${build_bat}"
  printf '@if errorlevel 1 exit /b 1\r\n' >> "${build_bat}"
  printf '@set DISTUTILS_USE_SDK=1\r\n' >> "${build_bat}"
  printf '@set MSSdk=1\r\n' >> "${build_bat}"
  if [[ "$WINDOWS_ARCH" == arm64 ]]; then
    printf '@set _PYTHON_HOST_PLATFORM=win-arm64\r\n' >> "${build_bat}"
    printf '@set CARGO_BUILD_TARGET=aarch64-pc-windows-msvc\r\n' >> "${build_bat}"
    # Tell pyo3-ffi's build script the target Python version so it can
    # compile the correct FFI bindings when cross-compiling.
    printf '@set PYO3_CROSS_PYTHON_VERSION=%s\r\n' "${PYTHON_VERSION}" >> "${build_bat}"
    # ring requires clang by name in PATH to compile ARM64 GAS assembly.
    # clang-cl.exe and clang.exe are the same LLVM binary; argv[0] picks mode.
    # IMPORTANT: %PATH% contains "(x86)" — setting PATH inside a cmd.exe
    # (...) block causes ") was unexpected" errors.  All PATH modifications
    # must be on standalone lines, never inside blocks.
    printf '@set "PATH=C:\\Program Files\\LLVM\\bin;%%PATH%%"\r\n' >> "${build_bat}"
    printf '@set "_CF=0"\r\n' >> "${build_bat}"
    printf '@where clang >nul 2>&1 && set "_CF=1"\r\n' >> "${build_bat}"
    # If clang.exe absent from choco LLVM bin, copy clang-cl.exe → clang.exe
    printf '@if "%%_CF%%"=="0" if exist "C:\\Program Files\\LLVM\\bin\\clang-cl.exe" copy /y "C:\\Program Files\\LLVM\\bin\\clang-cl.exe" "%%TEMP%%\\clang.exe" >nul 2>&1\r\n' >> "${build_bat}"
    # Fallback: search VS LLVM tree (%%VCINSTALLDIR%% set by vcvarsall)
    printf '@if "%%_CF%%"=="0" for /f "delims=" %%%%F in ('"'"'dir /s /b "%%VCINSTALLDIR%%Tools\\Llvm\\clang-cl.exe" 2^>nul'"'"') do copy /y "%%%%F" "%%TEMP%%\\clang.exe" >nul 2>&1\r\n' >> "${build_bat}"
    # Add TEMP to PATH only if we created clang.exe there (standalone line = safe)
    printf '@if "%%_CF%%"=="0" if exist "%%TEMP%%\\clang.exe" set "PATH=%%TEMP%%;%%PATH%%"\r\n' >> "${build_bat}"
    printf '@where clang 2>nul && echo clang: OK || echo WARNING: clang not found - ring ARM64 build will fail\r\n' >> "${build_bat}"
    # Ensure ARM64 MSVC lib dir is in LIB so link.exe finds legacy_stdio_definitions.lib
    # and other CRT libs.  vcvarsall x64_arm64 should set this, but the ARM64 component
    # may install under a different MSVC toolset version than VCToolsInstallDir points to.
    # Strategy 1: use VCToolsInstallDir directly (fast path, works when versions match).
    # Strategy 2: search all MSVC versions under VCINSTALLDIR for lib\arm64 (fallback).
    printf '@if defined VCToolsInstallDir set "LIB=%%VCToolsInstallDir%%lib\\arm64;%%LIB%%"\r\n' >> "${build_bat}"
    printf '@if defined VCINSTALLDIR for /d %%%%V in ("%%VCINSTALLDIR%%Tools\\MSVC\\*") do @if exist "%%%%V\\lib\\arm64" set "LIB=%%%%V\\lib\\arm64;%%LIB%%"\r\n' >> "${build_bat}"
    printf '@echo LIB (first entry): %%LIB:~0,120%%\r\n' >> "${build_bat}"
    # Strategy 3: if the MSVC ARM64 CRT libs are still missing (lib\arm64 not installed),
    # create minimal ARM64 import lib stubs using lib.exe /machine:ARM64.
    # A pure Rust cdylib does not call MSVC CRT functions directly (it uses Windows Heap
    # API and Rust's own compiler_builtins), so the linker only needs to OPEN these files
    # without resolving any symbols from them.  An empty import lib satisfies that.
    #
    # We use a temp sentinel file as a flag to avoid Windows batch delayed-expansion issues.
    # Def files are written from bash here; lib.exe runs inside the batch file after vcvarsall.
    mkdir -p "${crt_stub_dir}"
    # Each def declares a minimal import lib pointing to the real DLL name.
    # The LIBRARY name must match what the linker records in the import table (DLL name).
    printf 'LIBRARY MSVCRT\r\nEXPORTS\r\n'                     > "${crt_stub_dir}/msvcrt.def"
    printf 'LIBRARY VCRUNTIME140\r\nEXPORTS\r\n'               > "${crt_stub_dir}/vcruntime.def"
    printf 'LIBRARY _legacy_stdio_stub\r\nEXPORTS\r\n'         > "${crt_stub_dir}/legacy_stdio_definitions.def"
    printf 'LIBRARY _legacy_stdio_wide_stub\r\nEXPORTS\r\n'    > "${crt_stub_dir}/legacy_stdio_wide_specifiers.def"
    printf 'LIBRARY _oldnames_stub\r\nEXPORTS\r\n'             > "${crt_stub_dir}/oldnames.def"
    # Batch: use a temp file as the found-flag (avoids setlocal ENABLEDELAYEDEXPANSION).
    printf '@del /f /q "%%TEMP%%\\_arm64_crt_found.txt" 2>nul\r\n' >> "${build_bat}"
    printf '@if defined VCToolsInstallDir if exist "%%VCToolsInstallDir%%lib\\arm64\\msvcrt.lib" echo 1>"%%TEMP%%\\_arm64_crt_found.txt"\r\n' >> "${build_bat}"
    printf '@if defined VCINSTALLDIR for /d %%%%V in ("%%VCINSTALLDIR%%Tools\\MSVC\\*") do @if exist "%%%%V\\lib\\arm64\\msvcrt.lib" echo 1>"%%TEMP%%\\_arm64_crt_found.txt"\r\n' >> "${build_bat}"
    printf '@if not exist "%%TEMP%%\\_arm64_crt_found.txt" echo ARM64 MSVC CRT not found — creating stub libs for linker compatibility\r\n' >> "${build_bat}"
    for _crt in msvcrt vcruntime legacy_stdio_definitions legacy_stdio_wide_specifiers oldnames; do
      printf '@if not exist "%%TEMP%%\\_arm64_crt_found.txt" lib /nologo /machine:ARM64 /def:"%s" /out:"%s"\r\n' \
        "${crt_stub_dir_win}\\${_crt}.def" "${crt_stub_dir_win}\\${_crt}.lib" >> "${build_bat}"
    done
    printf '@if not exist "%%TEMP%%\\_arm64_crt_found.txt" set "LIB=%%LIB%%;%s"\r\n' "${crt_stub_dir_win}" >> "${build_bat}"
    printf '@if not exist "%%TEMP%%\\_arm64_crt_found.txt" echo ARM64 CRT stub libs created and added to LIB\r\n' >> "${build_bat}"
    printf '"%s" "%s\\scripts\\generate_arm64_importlib.py"\r\n' \
      "${python_exe_win}" "${project_dir_win}" >> "${build_bat}"
    printf '@if errorlevel 1 exit /b 1\r\n' >> "${build_bat}"
  fi
  printf 'uv build --wheel --python "%s" --out-dir "%s" "%s"\r\n' \
    "${python_exe_win}" "${built_wheel_dir_win}" "${project_dir_win}" >> "${build_bat}"

  if cmd.exe //c "${build_bat_win}" > "${BUILD_LOG}" 2>&1; then
    echo "Build completed successfully"
    export BUILT_WHEEL_FILE=$(ls "${BUILT_WHEEL_DIR}"/*.whl | head -n 1)
  else
    echo "Build failed! Dumping log:"
    cat "${BUILD_LOG}"
    section_end "build_wheel_function"
    exit 1
  fi

  section_end "build_wheel_function"
}

# ── MSVC setup ────────────────────────────────────────────────────────────────
# Finds (and installs if necessary) VS Build Tools + the VCTools workload.
# Exports VCVARSALL_PATH and VCVARSALL_ARCH for use by build_wheel.
# Does NOT import the MSVC environment into the bash session.
setup_msvc() {
  section_start "setup_msvc" "Setting up MSVC build environment"

  # ── Locate vswhere ──────────────────────────────────────────────────────────
  local vswhere
  local vswhere_candidates=(
    "C:/Program Files (x86)/Microsoft Visual Studio/Installer/vswhere.exe"
    "C:/Program Files/Microsoft Visual Studio/Installer/vswhere.exe"
  )
  _find_vswhere() {
    vswhere=""
    for candidate in "${vswhere_candidates[@]}"; do
      if [[ -f "$candidate" ]]; then vswhere="$candidate"; return; fi
    done
    vswhere=$(where.exe vswhere.exe 2>/dev/null | head -1 | tr -d '\r') || true
  }
  _find_vswhere

  # ── Install VS Build Tools if missing ──────────────────────────────────────
  if [[ -z "${vswhere:-}" || ! -f "$vswhere" ]]; then
    echo "VS Build Tools not found — installing now."
    echo "  (this is slow ~10-20 min; pre-install on runner image to avoid this)"

    local vs_installer="${WORK_DIR}/vs_buildtools.exe"
    if command -v choco &>/dev/null; then
      choco install visualstudio2022buildtools \
        --package-parameters "--add Microsoft.VisualStudio.Workload.VCTools --includeRecommended --passive --wait" \
        -y --no-progress
    else
      echo "  choco not found; downloading vs_BuildTools.exe from Microsoft..."
      curl -fsSL "https://aka.ms/vs/17/release/vs_BuildTools.exe" -o "$vs_installer"
      # Use a batch file to avoid MSYS2 quoting issues with paths containing spaces
      local install_bat install_bat_win vs_installer_win
      install_bat="${WORK_DIR}/install_vs.bat"
      install_bat_win=$(cygpath -w "$install_bat")
      vs_installer_win=$(cygpath -w "$vs_installer")
      printf '"%s" --quiet --wait --norestart --nocache --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended\r\n' \
        "${vs_installer_win}" > "${install_bat}"
      cmd.exe //c "${install_bat_win}"
    fi
    _find_vswhere
    [[ -n "${vswhere:-}" && -f "$vswhere" ]] \
      || { echo "ERROR: vswhere.exe still not found after VS Build Tools install" >&2; exit 1; }
  fi
  echo "Found vswhere.exe: $vswhere"

  # ── Get VS installation path ────────────────────────────────────────────────
  # -all includes "incomplete" installs (installer shell only, no workloads yet).
  local vs_path vs_path_unix
  vs_path=$("$vswhere" -all -products '*' -property installationPath 2>/dev/null | head -1 | tr -d '\r')
  [[ -n "$vs_path" ]] || { echo "ERROR: No Visual Studio installation found" >&2; exit 1; }
  echo "VS installation path: $vs_path"
  vs_path_unix=$(cygpath -u "${vs_path}" 2>/dev/null || echo "${vs_path//\\//}")

  # ── Ensure VCTools workload is installed ────────────────────────────────────
  if [[ ! -d "${vs_path_unix}/VC/Tools/MSVC" ]]; then
    echo "VCTools workload missing — adding it (this may take several minutes)..."
    local vs_setup="C:/Program Files (x86)/Microsoft Visual Studio/Installer/setup.exe"
    local vctools_bat vctools_bat_win vs_setup_win vs_path_win
    vctools_bat="${WORK_DIR}/add_vctools.bat"
    vctools_bat_win=$(cygpath -w "$vctools_bat")
    vs_setup_win=$(cygpath -w "$vs_setup" 2>/dev/null || echo "$vs_setup")
    vs_path_win=$(cygpath -w "$vs_path_unix" 2>/dev/null || echo "$vs_path")
    printf '"%s" modify --installPath "%s" --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended --quiet --wait --norestart\r\n' \
      "${vs_setup_win}" "${vs_path_win}" > "${vctools_bat}"
    cmd.exe //c "${vctools_bat_win}" || true
    [[ -d "${vs_path_unix}/VC/Tools/MSVC" ]] \
      || { echo "ERROR: VCTools workload still missing after install attempt" >&2; exit 1; }
  fi
  echo "VC/Tools/MSVC versions: $(ls "${vs_path_unix}/VC/Tools/MSVC" | tr '\n' ' ')"

  local vcvarsall="${vs_path_unix}/VC/Auxiliary/Build/vcvarsall.bat"
  [[ -f "$vcvarsall" ]] || { echo "ERROR: vcvarsall.bat not found at: $vcvarsall" >&2; exit 1; }
  local vcvarsall_win
  vcvarsall_win=$(cygpath -w "${vcvarsall}")

  # ── Probe for a working vcvarsall arch ─────────────────────────────────────
  # Test without importing MSVC env into bash: run a batch file that calls
  # vcvarsall and checks `where cl.exe`.  x64_x86 (cross-compile) requires an
  # optional component; fall back to x86 (32-bit native tools via WOW64) if it
  # isn't installed.
  local probe_bat probe_bat_win found_arch=""
  probe_bat="${WORK_DIR}/vcvars_probe.bat"
  probe_bat_win=$(cygpath -w "${probe_bat}")

  local arches_to_try=("${VC_ARCH}")
  [[ "${VC_ARCH}" == "x64_x86" ]] && arches_to_try+=("x86")

  for try_arch in "${arches_to_try[@]}"; do
    printf '@call "%s" %s > NUL 2>&1\r\n@if errorlevel 1 exit /b 1\r\n@where cl.exe > NUL 2>&1\r\n' \
      "${vcvarsall_win}" "${try_arch}" > "${probe_bat}"
    if cmd.exe //c "${probe_bat_win}" > /dev/null 2>&1; then
      found_arch="${try_arch}"
      echo "vcvarsall arch: ${found_arch} (ok)"
      break
    else
      echo "vcvarsall arch: ${try_arch} (cl.exe not found, skipping)"
    fi
  done

  if [[ -z "${found_arch}" && "${VC_ARCH}" == "x64_arm64" ]]; then
    echo "ARM64 cross-tools not found — adding VC.Tools.ARM64 component..."
    local vs_setup="C:/Program Files (x86)/Microsoft Visual Studio/Installer/setup.exe"
    local arm64_bat arm64_bat_win vs_setup_win vs_path_win
    arm64_bat="${WORK_DIR}/add_arm64_tools.bat"
    arm64_bat_win=$(cygpath -w "${arm64_bat}")
    vs_setup_win=$(cygpath -w "$vs_setup" 2>/dev/null || echo "$vs_setup")
    vs_path_win=$(cygpath -w "$vs_path_unix" 2>/dev/null || echo "$vs_path")
    printf '"%s" modify --installPath "%s" --add Microsoft.VisualStudio.Workload.VCTools --add Microsoft.VisualStudio.Component.VC.Tools.ARM64 --includeRecommended --quiet --wait --norestart\r\n' \
      "${vs_setup_win}" "${vs_path_win}" > "${arm64_bat}"
    cmd.exe //c "${arm64_bat_win}" || true
    # Diagnostic: list MSVC lib directories to verify ARM64 CRT libs exist
    echo "=== MSVC lib directories after ARM64 component install ==="
    local msvc_root="${vs_path_unix}/VC/Tools/MSVC"
    for ver_dir in "${msvc_root}"/*/; do
      echo "  ${ver_dir}lib/: $(ls "${ver_dir}lib/" 2>/dev/null | tr '\n' ' ' || echo '(empty/missing)')"
    done
    # Verify ARM64 lib directory was actually created
    local arm64_lib_found=false
    for ver_dir in "${msvc_root}"/*/; do
      if [[ -d "${ver_dir}lib/arm64" ]]; then
        arm64_lib_found=true
        break
      fi
    done
    if [[ "$arm64_lib_found" != "true" ]]; then
      echo "ERROR: lib/arm64 still not found after VC.Tools.ARM64 install — ARM64 CRT missing"
      echo "  Installed MSVC versions: $(ls "${msvc_root}" 2>/dev/null | tr '\n' ' ')"
      echo "  This likely means the VS component install didn't include ARM64 runtime libraries."
      echo "  Try pre-installing on the runner image with: --add Microsoft.VisualStudio.ComponentGroup.NativeDesktop.Core --add Microsoft.VisualStudio.Component.VC.Tools.ARM64 --includeRecommended"
    fi
    # Re-probe after installing ARM64 tools
    for try_arch in "${arches_to_try[@]}"; do
      printf '@call "%s" %s > NUL 2>&1\r\n@if errorlevel 1 exit /b 1\r\n@where cl.exe > NUL 2>&1\r\n' \
        "${vcvarsall_win}" "${try_arch}" > "${probe_bat}"
      if cmd.exe //c "${probe_bat_win}" > /dev/null 2>&1; then
        found_arch="${try_arch}"
        echo "vcvarsall arch: ${found_arch} (ok after installing ARM64 tools)"
        break
      fi
    done
  fi

  # ── Ensure LLVM/Clang for ARM64 cross-compilation ───────────────────────────
  # ring (a Rust crypto crate) uses clang by name to compile ARM64 GAS assembly.
  # CC_* env vars don't help — ring calls cc::Build::compiler("clang") directly.
  # Ensure clang.exe is installed via choco; the bat file also adds its path.
  if [[ "${VC_ARCH}" == "x64_arm64" ]]; then
    local llvm_bin="/c/Program Files/LLVM/bin"
    if [[ ! -f "${llvm_bin}/clang.exe" ]]; then
      echo "clang not found — installing LLVM via PowerShell download..."
      local llvm_version="19.1.7"
      local llvm_installer_win="C:\\llvm-installer.exe"
      powershell.exe -NoProfile -Command \
        "Invoke-WebRequest -Uri 'https://github.com/llvm/llvm-project/releases/download/llvmorg-${llvm_version}/LLVM-${llvm_version}-win64.exe' -OutFile '${llvm_installer_win}'"
      # /S = silent, /D= sets install dir (must be last arg, no quotes around path)
      cmd.exe //c "${llvm_installer_win} /S /D=C:\\Program Files\\LLVM" || true
      rm -f "$(cygpath -u "${llvm_installer_win}")" 2>/dev/null || true
    fi
    echo "clang present: $([[ -f "${llvm_bin}/clang.exe" ]] && echo yes || echo no)"
  fi

  if [[ -z "${found_arch}" ]]; then
    echo "ERROR: no working vcvarsall arch found (tried: ${arches_to_try[*]})" >&2
    # Show vcvarsall output for diagnostics
    local diag_bat diag_bat_win
    diag_bat="${WORK_DIR}/vcvars_diag.bat"
    diag_bat_win=$(cygpath -w "$diag_bat")
    printf '@echo on\r\ncall "%s" %s\r\n' "${vcvarsall_win}" "${VC_ARCH}" > "${diag_bat}"
    cmd.exe //c "${diag_bat_win}" || true
    exit 1
  fi

  # Export for use by build_wheel — MSVC env itself stays out of bash.
  export VCVARSALL_PATH="${vcvarsall}"
  export VCVARSALL_ARCH="${found_arch}"
  echo "VCVARSALL_PATH: ${VCVARSALL_PATH}"
  section_end "setup_msvc"
}

# ── 32-bit VC++ runtime (x86 only) ───────────────────────────────────────────
setup_vcredist_x86() {
  [[ "$WINDOWS_ARCH" == x86 ]] || return 0
  section_start "install_vcredist_x86" "Installing 32-bit VC++ Redistributable"

  # Locate VS Build Tools to copy 32-bit CRT DLLs directly into SysWOW64.
  # This takes effect immediately (no reboot), unlike the vc_redist.x86.exe installer.
  local vswhere="C:/Program Files (x86)/Microsoft Visual Studio/Installer/vswhere.exe"
  if [[ -f "$vswhere" ]]; then
    local vs_path vs_path_unix
    vs_path=$("$vswhere" -all -products '*' -property installationPath 2>/dev/null | head -1 | tr -d '\r')
    vs_path_unix=$(cygpath -u "${vs_path}" 2>/dev/null || echo "${vs_path//\\//}")
    if [[ -n "$vs_path_unix" ]]; then
      local syswow64="/c/Windows/SysWOW64"
      for crt_dir in "${vs_path_unix}"/VC/Redist/MSVC/*/x86/Microsoft.VC*.CRT; do
        [[ -d "$crt_dir" ]] || continue
        echo "Copying x86 CRT DLLs from $crt_dir → $syswow64"
        cp "$crt_dir"/*.dll "$syswow64/" && echo "Done." || echo "Copy failed (may already exist)"
      done
    fi
  fi

  # Also run the installer as a belt-and-suspenders measure (errors are non-fatal)
  local vcredist="${WORK_DIR}/vc_redist.x86.exe"
  curl -fsSL "https://aka.ms/vs/17/release/vc_redist.x86.exe" -o "$vcredist"
  cmd.exe //c "$(cygpath -w "$vcredist") /install /quiet /norestart" || true

  section_end "install_vcredist_x86"
}

# ── System diagnostics ────────────────────────────────────────────────────────
debug_system_info() {
  section_start "debug_system_info" "System diagnostics"

  echo "=== Runner identity ==="
  echo "HOSTNAME: ${COMPUTERNAME:-unknown}"
  echo "USERNAME: ${USERNAME:-${USER:-unknown}}"
  echo "WINDOWS_ARCH: ${WINDOWS_ARCH}"
  echo "PYTHON_VERSION: ${PYTHON_VERSION}"

  echo ""
  echo "=== OS ==="
  cmd.exe //c "ver" 2>/dev/null || true
  powershell.exe -NoProfile -Command \
    "(Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion' |
      Select-Object ProductName,DisplayVersion,CurrentBuild,UBR |
      Format-List | Out-String).Trim()" 2>/dev/null || true

  echo ""
  echo "=== Architecture ==="
  echo "PROCESSOR_ARCHITECTURE: ${PROCESSOR_ARCHITECTURE:-unknown}"
  echo "PROCESSOR_ARCHITEW6432 (WOW64 host arch): ${PROCESSOR_ARCHITEW6432:-not set (native)}"
  if [[ -d "/c/Windows/SysWOW64" ]]; then
    echo "SysWOW64: present"
    ls "/c/Windows/SysWOW64/vcruntime140.dll" 2>/dev/null && echo "  vcruntime140.dll (x86): FOUND" || echo "  vcruntime140.dll (x86): NOT FOUND"
  else
    echo "SysWOW64: NOT FOUND — 32-bit (WOW64) subsystem is not installed"
  fi

  echo ""
  echo "=== Disk space ==="
  powershell.exe -NoProfile -Command \
    "Get-PSDrive C | Select-Object @{N='Used(GB)';E={[math]::Round(\$_.Used/1GB,1)}},@{N='Free(GB)';E={[math]::Round(\$_.Free/1GB,1)}} | Format-Table -AutoSize | Out-String" 2>/dev/null || true

  echo ""
  echo "=== Key tools ==="
  for tool in choco winget cmake ninja git curl powershell.exe uv rustc cargo cl.exe clang; do
    local path
    path=$(command -v "$tool" 2>/dev/null || where.exe "$tool" 2>/dev/null | head -1 | tr -d '\r' || true)
    if [[ -n "$path" ]]; then
      echo "  $tool: $path"
    else
      echo "  $tool: NOT FOUND"
    fi
  done

  echo ""
  echo "=== Visual Studio / MSVC ==="
  local vswhere_candidates=(
    "C:/Program Files (x86)/Microsoft Visual Studio/Installer/vswhere.exe"
    "C:/Program Files/Microsoft Visual Studio/Installer/vswhere.exe"
  )
  local vswhere=""
  for candidate in "${vswhere_candidates[@]}"; do
    if [[ -f "$candidate" ]]; then vswhere="$candidate"; break; fi
  done
  if [[ -n "$vswhere" ]]; then
    echo "  vswhere.exe: $vswhere"
    "$vswhere" -all -products '*' -format json 2>/dev/null \
      | powershell.exe -NoProfile -Command \
          "\$in = \$input | Out-String;
           if (\$in.Trim()) {
             (\$in | ConvertFrom-Json) |
               Select-Object -Property displayName,installationVersion,installationPath |
               Format-List | Out-String
           } else { 'No VS installations found via vswhere' }" 2>/dev/null || true

    local vs_path vs_path_unix
    vs_path=$("$vswhere" -all -products '*' -property installationPath 2>/dev/null | head -1 | tr -d '\r')
    vs_path_unix=$(cygpath -u "${vs_path}" 2>/dev/null || echo "${vs_path//\\//}")
    local vc_tools_dir="${vs_path_unix}/VC/Tools/MSVC"
    if [[ -d "$vc_tools_dir" ]]; then
      echo "  VC/Tools/MSVC versions: $(ls "$vc_tools_dir" | tr '\n' ' ')"
      local sample_cl
      sample_cl=$(ls "${vc_tools_dir}"/*/bin/Hostx64/x64/cl.exe 2>/dev/null | head -1)
      echo "  cl.exe (x64):  ${sample_cl:-NOT FOUND}"
      sample_cl=$(ls "${vc_tools_dir}"/*/bin/Hostx64/x86/cl.exe 2>/dev/null | head -1)
      echo "  cl.exe (x86):  ${sample_cl:-NOT FOUND}"
      sample_cl=$(ls "${vc_tools_dir}"/*/bin/Hostx64/arm64/cl.exe 2>/dev/null | head -1 || true)
      echo "  cl.exe (arm64 cross): ${sample_cl:-NOT FOUND}"
      local armasm64
      armasm64=$(ls "${vc_tools_dir}"/*/bin/Hostx64/arm64/armasm64.exe 2>/dev/null | head -1 || true)
      echo "  armasm64.exe: ${armasm64:-NOT FOUND}"
    else
      echo "  VC/Tools/MSVC: NOT FOUND at ${vc_tools_dir}"
      echo "  (VS shell may be installed but VCTools workload is missing)"
    fi
    local llvm_dir="${vs_path_unix}/VC/Tools/Llvm"
    if [[ -d "$llvm_dir" ]]; then
      echo "  VC/Tools/Llvm: $(ls "$llvm_dir" 2>/dev/null | tr '\n' ' ')"
      local clang_path
      for clang_path in "${llvm_dir}/x64/bin/clang.exe" "${llvm_dir}/bin/clang.exe"; do
        [[ -f "$clang_path" ]] && echo "  clang.exe: ${clang_path}" && break
      done
    else
      echo "  VC/Tools/Llvm: NOT FOUND"
    fi

    echo "  Installed packages (workloads/components via vswhere):"
    "$vswhere" -all -products '*' -property packages 2>/dev/null \
      | grep -i 'workload\|VC\.Tools\|VC\.Redist' | head -20 || echo "    (none matched or vswhere -property packages not supported)"
  else
    echo "  vswhere.exe: NOT FOUND"
    echo "  Searching for VS directories:"
    for dir in \
      "C:/Program Files/Microsoft Visual Studio" \
      "C:/Program Files (x86)/Microsoft Visual Studio" \
      "C:/BuildTools"; do
      if [[ -d "$dir" ]]; then
        echo "    $dir: $(ls "$dir" 2>/dev/null | tr '\n' ' ')"
      else
        echo "    $dir: not found"
      fi
    done
  fi

  echo ""
  echo "=== Windows SDK ==="
  for sdk_dir in \
    "C:/Program Files (x86)/Windows Kits/10" \
    "C:/Program Files/Windows Kits/10"; do
    if [[ -d "$sdk_dir" ]]; then
      echo "  Windows 10 SDK: $sdk_dir"
      ls "$sdk_dir/Include/" 2>/dev/null | tail -5 || true
    fi
  done

  echo ""
  echo "=== PATH ==="
  echo "$PATH" | tr ':' '\n' | sed 's/^/  /'

  echo ""
  echo "=== Chocolatey packages (if choco available) ==="
  if command -v choco &>/dev/null; then
    choco list --local-only 2>/dev/null | head -40 || true
  else
    echo "  choco not available"
  fi

  section_end "debug_system_info"
}

# ── Main ──────────────────────────────────────────────────────────────────────
# Execution order is deliberate:
#   1. setup_msvc  — find/install VS; probe vcvarsall arch; export VCVARSALL_*
#                    Does NOT import MSVC env into bash (that corrupts APPDATA
#                    and other vars that uv/curl depend on).
#   2. setup_vcredist_x86 — copy x86 CRT DLLs into SysWOW64 (x86 builds only;
#                           needs vswhere which setup_msvc already found)
#   3. setup_python — install uv + Python; for x86, CRT DLLs are now present
#   4. setup_rust   — install Rust toolchain
#   5. build_wheel  — runs vcvarsall + uv build inside a .bat file; MSVC env
#                     is scoped to this subprocess only
setup_env

# On Windows GitLab runners, jobs run as the SYSTEM account whose profile is
# C:\Windows\system32\config\systemprofile. This puts APPDATA, TEMP, etc.
# under system32. The 32-bit HostX86 cross-compiler has system32
# WOW64-redirected to SysWOW64, making files under SYSTEM's profile
# invisible — causing "Cannot open include file: Python.h".
#
# Redirect everything to the project dir which is outside system32.
_win_project_dir="$(cygpath -w "${PROJECT_DIR}")"
export UV_DATA_DIR="${_win_project_dir}\\.uv"
export UV_CACHE_DIR="${_win_project_dir}\\.uv-cache"
# UV_PYTHON_INSTALL_DIR controls where uv installs AND searches for managed
# Python versions — this is distinct from UV_DATA_DIR and is what determines
# the Python include dir passed to cl.exe via -I flags.
export UV_PYTHON_INSTALL_DIR="${_win_project_dir}\\.uv\\python"
# CARGO_HOME and RUSTUP_HOME default to the SYSTEM account's profile under
# system32, making them invisible to 32-bit subprocesses (WOW64 redirects
# system32 → SysWOW64).  setup.py calls subprocess.run(["cargo", ...]) from
# a 32-bit Python process on x86 builds, so cargo must live outside system32.
export CARGO_HOME="${_win_project_dir}\\.cargo"
export RUSTUP_HOME="${_win_project_dir}\\.rustup"
export TEMP="${_win_project_dir}\\.tmp"
export TMP="${TEMP}"
mkdir -p "$(cygpath -u "${TEMP}")"
debug_system_info
setup_msvc
setup_vcredist_x86
setup_python
setup_rust
build_wheel
repair_wheel
finalize
# ARM64 binaries cannot execute on the x64 runner — skip smoke test
if [[ "$WINDOWS_ARCH" != "arm64" ]]; then
  test_wheel
fi

if command -v sccache &>/dev/null; then
  sccache --show-stats || true
fi
