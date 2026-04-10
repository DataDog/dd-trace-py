#!/usr/bin/env bash
# Build Windows wheels for dd-trace-py using uv.
# Runs under Git Bash (bash.exe from Git for Windows) on windows-v2:2022 runners.
#
# Required env vars:
#   PYTHON_VERSIONS  Space-separated list of Python versions to build (e.g. "3.12 3.13 3.14")
#   WINDOWS_ARCH     Target architecture: "amd64" or "x86" (default: "amd64")
#
# MSVC setup runs once per job; all Python versions share the same toolchain.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/build-wheel-helpers.sh"

# ── Windows config ────────────────────────────────────────────────────────────
PYTHON_VERSIONS=${PYTHON_VERSIONS:?PYTHON_VERSIONS is required (space-separated, e.g. "3.12 3.13 3.14")}
WINDOWS_ARCH=${WINDOWS_ARCH:-amd64}

case "$WINDOWS_ARCH" in
    x86)   UV_PYTHON_PLATFORM="windows-x86";    VC_ARCH="x64_x86" ;;
    amd64) UV_PYTHON_PLATFORM="windows-x86_64"; VC_ARCH="amd64"   ;;
    *)     echo "ERROR: Unsupported WINDOWS_ARCH=$WINDOWS_ARCH" >&2; exit 1 ;;
esac

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
  build_bat="${WORK_DIR}/build.bat"
  build_bat_win=$(cygpath -w "${build_bat}")
  vcvarsall_win=$(cygpath -w "${VCVARSALL_PATH}")
  built_wheel_dir_win=$(cygpath -w "${BUILT_WHEEL_DIR}")
  project_dir_win=$(cygpath -w "${PROJECT_DIR}")
  python_exe_win=$(cygpath -w "${PYTHON_EXE}")

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
#
# Flow:
#   1. Locate vswhere (install VS Build Tools with retries if missing)
#   2. Find VS installation path (prefer complete installs with VCTools)
#   3. Probe vcvarsall immediately — fast-path exits here on pre-configured runners
#   4. Only if the probe fails: install/repair VCTools workload (with retries)
#   5. Re-probe after install; dump diagnostics on failure
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
    local max_vs_attempts=3
    for attempt in $(seq 1 $max_vs_attempts); do
      echo "  VS Build Tools install attempt $attempt of $max_vs_attempts..."
      if command -v choco &>/dev/null; then
        choco install visualstudio2022buildtools \
          --package-parameters "--add Microsoft.VisualStudio.Workload.VCTools --includeRecommended --passive --wait" \
          -y --no-progress && break
      else
        echo "  choco not found; downloading vs_BuildTools.exe from Microsoft..."
        curl -fsSL "https://aka.ms/vs/17/release/vs_BuildTools.exe" -o "$vs_installer"
        local install_bat install_bat_win vs_installer_win
        install_bat="${WORK_DIR}/install_vs.bat"
        install_bat_win=$(cygpath -w "$install_bat")
        vs_installer_win=$(cygpath -w "$vs_installer")
        printf '"%s" --quiet --wait --norestart --nocache --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended\r\n' \
          "${vs_installer_win}" > "${install_bat}"
        cmd.exe //c "${install_bat_win}" && break
      fi
      echo "  install attempt $attempt failed"
      [[ $attempt -eq $max_vs_attempts ]] && { echo "ERROR: VS Build Tools install failed after $max_vs_attempts attempts" >&2; exit 1; }
      sleep 15
    done
    _find_vswhere
    [[ -n "${vswhere:-}" && -f "$vswhere" ]] \
      || { echo "ERROR: vswhere.exe still not found after VS Build Tools install" >&2; exit 1; }
  fi
  echo "Found vswhere.exe: $vswhere"

  # ── Get VS installation path ────────────────────────────────────────────────
  # Prefer installations that already have VCTools to avoid picking a broken/incomplete one.
  # Fall back to -all (which includes incomplete installs) only if nothing complete is found.
  local vs_path vs_path_unix
  vs_path=$("$vswhere" -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 \
    -products '*' -property installationPath 2>/dev/null | head -1 | tr -d '\r')
  if [[ -z "$vs_path" ]]; then
    echo "No complete VCTools installation found; falling back to -all (may need workload install)"
    vs_path=$("$vswhere" -all -products '*' -property installationPath 2>/dev/null | head -1 | tr -d '\r')
  fi
  [[ -n "$vs_path" ]] || { echo "ERROR: No Visual Studio installation found" >&2; exit 1; }
  echo "VS installation path: $vs_path"
  vs_path_unix=$(cygpath -u "${vs_path}" 2>/dev/null || echo "${vs_path//\\//}")

  local vcvarsall="${vs_path_unix}/VC/Auxiliary/Build/vcvarsall.bat"

  # ── Probe helper ───────────────────────────────────────────────────────────
  # Writes and runs a batch file that activates vcvarsall and verifies both
  # cl.exe (compiler) and link.exe (linker, needs Windows SDK on LIB path).
  # Sets found_arch on success, returns non-zero on failure.
  local probe_bat probe_bat_win found_arch=""
  probe_bat="${WORK_DIR}/vcvars_probe.bat"
  probe_bat_win=$(cygpath -w "${probe_bat}")
  local arches_to_try=("${VC_ARCH}")
  [[ "${VC_ARCH}" == "x64_x86" ]] && arches_to_try+=("x86")

  _run_probe() {
    found_arch=""
    [[ -f "$vcvarsall" ]] || return 1
    local vcvarsall_win
    vcvarsall_win=$(cygpath -w "${vcvarsall}")
    for try_arch in "${arches_to_try[@]}"; do
      {
        printf '@call "%s" %s > NUL 2>&1\r\n' "${vcvarsall_win}" "${try_arch}"
        printf '@if errorlevel 1 exit /b 1\r\n'
        printf '@where cl.exe > NUL 2>&1\r\n'
        printf '@if errorlevel 1 exit /b 1\r\n'
        printf '@where link.exe > NUL 2>&1\r\n'
        printf '@if errorlevel 1 exit /b 1\r\n'
      } > "${probe_bat}"
      if cmd.exe //c "${probe_bat_win}" > /dev/null 2>&1; then
        found_arch="${try_arch}"
        echo "vcvarsall arch: ${found_arch} (cl.exe + link.exe ok)"
        return 0
      else
        echo "vcvarsall arch: ${try_arch} (cl.exe or link.exe not found, skipping)"
      fi
    done
    return 1
  }

  # ── Fast-path: probe immediately ───────────────────────────────────────────
  # On pre-configured runners with MSVC already installed, this succeeds and
  # we skip the workload install entirely.
  if _run_probe; then
    echo "MSVC already functional — skipping workload install"
  else
    # ── Install/repair VCTools workload ────────────────────────────────────
    echo "vcvarsall probe failed — attempting to install/repair VCTools workload..."
    echo "VC/Tools/MSVC: $(ls "${vs_path_unix}/VC/Tools/MSVC" 2>/dev/null | tr '\n' ' ' || echo '(not found)')"

    # Kill any lingering VS installer processes that could hold file locks.
    taskkill.exe /F /IM setup.exe /T 2>/dev/null || true
    taskkill.exe /F /IM vs_installer.exe /T 2>/dev/null || true
    sleep 3

    local vs_setup="C:/Program Files (x86)/Microsoft Visual Studio/Installer/setup.exe"
    local vctools_bat vctools_bat_win vs_setup_win vs_path_win
    local vctools_log="${WORK_DIR}/vctools_install.log"
    vctools_bat="${WORK_DIR}/add_vctools.bat"
    vctools_bat_win=$(cygpath -w "$vctools_bat")
    vs_setup_win=$(cygpath -w "$vs_setup" 2>/dev/null || echo "$vs_setup")
    vs_path_win=$(cygpath -w "$vs_path_unix" 2>/dev/null || echo "$vs_path")
    # Use --passive (not --quiet) so the installer writes progress to the log.
    # --log captures installer diagnostics; shown on failure.
    printf '"%s" modify --installPath "%s" --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended --passive --wait --norestart --log "%s"\r\n' \
      "${vs_setup_win}" "${vs_path_win}" "$(cygpath -w "${vctools_log}")" > "${vctools_bat}"

    local repaired=false
    local max_workload_attempts=3
    for attempt in $(seq 1 $max_workload_attempts); do
      echo "  VCTools workload repair attempt $attempt of $max_workload_attempts..."
      local exit_code=0
      cmd.exe //c "${vctools_bat_win}" || exit_code=$?
      # Exit code 3010 = reboot required but installation completed — treat as success.
      if [[ $exit_code -eq 0 || $exit_code -eq 3010 ]]; then
        repaired=true
        break
      fi
      echo "  repair attempt $attempt failed (exit code: $exit_code)"
      if [[ -f "$vctools_log" ]]; then
        echo "  Installer log tail:"
        tail -20 "$vctools_log" | sed 's/^/    /'
      fi
      [[ $attempt -lt $max_workload_attempts ]] && sleep 15
    done

    if [[ "$repaired" != "true" ]]; then
      # Repair exhausted — the existing installation is unrecoverable via setup.exe.
      # Download and run a fresh vs_BuildTools.exe installer as a last resort.
      echo "Repair exhausted — falling back to fresh VS Build Tools install..."
      local vs_installer="${WORK_DIR}/vs_buildtools_fresh.exe"
      local fresh_bat fresh_bat_win vs_installer_win
      fresh_bat="${WORK_DIR}/install_vs_fresh.bat"
      fresh_bat_win=$(cygpath -w "$fresh_bat")
      vs_installer_win=$(cygpath -w "$vs_installer")
      if curl -fsSL "https://aka.ms/vs/17/release/vs_BuildTools.exe" -o "$vs_installer"; then
        printf '"%s" --wait --norestart --nocache --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended --log "%s"\r\n' \
          "${vs_installer_win}" "$(cygpath -w "${vctools_log}")" > "${fresh_bat}"
        local fresh_exit=0
        cmd.exe //c "${fresh_bat_win}" || fresh_exit=$?
        if [[ $fresh_exit -eq 0 || $fresh_exit -eq 3010 ]]; then
          echo "  Fresh install succeeded (exit code: $fresh_exit)"
          repaired=true
        else
          echo "  Fresh install failed (exit code: $fresh_exit)"
          if [[ -f "$vctools_log" ]]; then
            echo "  Installer log tail:"
            tail -20 "$vctools_log" | sed 's/^/    /'
          fi
        fi
      else
        echo "  Failed to download vs_BuildTools.exe — no network access?"
      fi
    fi

    [[ "$repaired" == "true" ]] \
      || { echo "ERROR: VCTools workload install failed — all strategies exhausted" >&2; exit 1; }
    [[ -d "${vs_path_unix}/VC/Tools/MSVC" ]] \
      || { echo "ERROR: VCTools workload still missing after install" >&2; exit 1; }
    echo "VC/Tools/MSVC versions: $(ls "${vs_path_unix}/VC/Tools/MSVC" | tr '\n' ' ')"

    # Re-probe after workload install
    if ! _run_probe; then
      # Dump vcvarsall output for diagnostics before failing
      local diag_bat diag_bat_win
      diag_bat="${WORK_DIR}/vcvars_diag.bat"
      diag_bat_win=$(cygpath -w "$diag_bat")
      printf '@echo on\r\ncall "%s" %s\r\n' "$(cygpath -w "${vcvarsall}")" "${VC_ARCH}" > "${diag_bat}"
      cmd.exe //c "${diag_bat_win}" || true
      echo "ERROR: no working vcvarsall arch found after workload install (tried: ${arches_to_try[*]})" >&2
      exit 1
    fi
  fi

  # Export for use by build_wheel — MSVC env itself stays out of bash.
  [[ -f "$vcvarsall" ]] || { echo "ERROR: vcvarsall.bat not found at: $vcvarsall" >&2; exit 1; }
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
  echo "PYTHON_VERSIONS: ${PYTHON_VERSIONS}"

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
  for tool in choco winget cmake ninja git curl powershell.exe uv rustc cargo cl.exe; do
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
      sample_cl=$(ls "${vc_tools_dir}"/*/bin/Hostx64/x64/cl.exe 2>/dev/null | head -1 || true)
      echo "  cl.exe (x64):  ${sample_cl:-NOT FOUND}"
      sample_cl=$(ls "${vc_tools_dir}"/*/bin/Hostx64/x86/cl.exe 2>/dev/null | head -1 || true)
      echo "  cl.exe (x86):  ${sample_cl:-NOT FOUND}"
    else
      echo "  VC/Tools/MSVC: NOT FOUND at ${vc_tools_dir}"
      echo "  (VS shell may be installed but VCTools workload is missing)"
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
#   3. setup_rust   — install Rust toolchain (shared across all Python versions)
#   4. Per-version loop — setup_python, build_wheel, repair_wheel, finalize,
#                         test_wheel for each version in PYTHON_VERSIONS
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
setup_rust

for PYTHON_VERSION in ${PYTHON_VERSIONS}; do
  export PYTHON_VERSION
  export UV_PYTHON="cpython-${PYTHON_VERSION}-${UV_PYTHON_PLATFORM}"

  # Clear per-build directories so each version starts clean.
  rm -rf "${BUILT_WHEEL_DIR:?}" "${TMP_WHEEL_DIR:?}" "${WORK_DIR:?}/test_wheel"
  mkdir -p "${BUILT_WHEEL_DIR}" "${TMP_WHEEL_DIR}"

  setup_python
  build_wheel
  repair_wheel
  finalize
  test_wheel
done

if command -v sccache &>/dev/null; then
  sccache --show-stats || true
fi
