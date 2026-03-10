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
    x86)   UV_PYTHON_PLATFORM="windows-x86";    VC_ARCH="x64_x86" ;;
    amd64) UV_PYTHON_PLATFORM="windows-x86_64"; VC_ARCH="amd64"   ;;
    *)     echo "ERROR: Unsupported WINDOWS_ARCH=$WINDOWS_ARCH" >&2; exit 1 ;;
esac
export UV_PYTHON="cpython-${PYTHON_VERSION}-${UV_PYTHON_PLATFORM}"

# ── Windows overrides ─────────────────────────────────────────────────────────

# Install the chosen Python via uv (not pre-installed as on manylinux/macOS).
# --force replaces any pre-existing shim not managed by uv.
setup_python() {
  section_start "setup_python" "Setting up Python ${UV_PYTHON} (${WINDOWS_ARCH})"
  export PATH="${UV_INSTALL_DIR:-$HOME/.local/bin}:${PATH}"
  if ! command -v uv &>/dev/null; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
  fi
  uv python install --force "${UV_PYTHON}"
  # Export the concrete executable path for use in repair/test steps
  export PYTHON_EXE
  PYTHON_EXE=$(uv python find "${UV_PYTHON}")
  echo "Python: $PYTHON_EXE"
  "$PYTHON_EXE" --version
  section_end "setup_python"
}

# Extend Rust setup: add the i686 cross-compile target for win32 builds.
setup_rust() {
  # Call the shared setup (installs rustup/rustc if missing, sets default stable)
  # Defined in build-wheel-helpers.sh
  section_start "install_rust" "Rust toolchain"
  export PATH="${CARGO_HOME:-$HOME/.cargo}/bin:${PATH}"
  if ! command -v rustc &>/dev/null; then
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
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
# build-wheel-helpers.sh uses sysconfig.get_config_var('BUILD_GNU_TYPE') which
# returns None on Windows.  Override to use a Windows-compatible tag instead.
build_wheel() {
  section_start "build_wheel_function" "Building wheel"

  PYTHON_VER=$(uv run --no-project python -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')")
  PYTHON_TAG=$(uv run --no-project python -c "
import sys
ver = f'{sys.version_info[0]}{sys.version_info[1]}'
plat = 'win_amd64' if sys.maxsize > 2**32 else 'win32'
print(f'cp{ver}-{plat}')
")

  export BUILD_LOG="${DEBUG_WHEEL_DIR}/build_${PYTHON_TAG}.log"
  echo "Building wheel for Python ${PYTHON_VER} (log: ${BUILD_LOG})"

  if uv build --wheel --out-dir "${BUILT_WHEEL_DIR}" . > "${BUILD_LOG}" 2>&1; then
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
# In bash, && inside a double-quoted string is literal — cmd.exe sees it as a
# chain operator — so this avoids the PowerShell 5.x parser quirk entirely.
setup_msvc() {
  section_start "setup_msvc" "Setting up MSVC build environment"
  # Check for cl.exe (MSVC compiler), NOT link.exe — Git for Windows ships a
  # Unix link utility at /usr/bin/link.exe which would give a false positive.
  if command -v cl.exe &>/dev/null; then
    echo "MSVC already in PATH (cl.exe: $(command -v cl.exe))"
    section_end "setup_msvc"
    return
  fi

  # Locate vswhere.exe — check the standard installer path first, then PATH.
  local vswhere
  local vswhere_candidates=(
    "C:/Program Files (x86)/Microsoft Visual Studio/Installer/vswhere.exe"
    "C:/Program Files/Microsoft Visual Studio/Installer/vswhere.exe"
  )
  for candidate in "${vswhere_candidates[@]}"; do
    if [[ -f "$candidate" ]]; then
      vswhere="$candidate"
      break
    fi
  done
  if [[ -z "${vswhere:-}" ]]; then
    vswhere=$(where.exe vswhere.exe 2>/dev/null | head -1 | tr -d '\r') || true
  fi
  if [[ -z "${vswhere:-}" || ! -f "$vswhere" ]]; then
    echo "VS Build Tools not found. Attempting installation via Chocolatey..."
    echo "  (this is slow ~10-20 min; pre-install on runner image to avoid this)"
    if ! command -v choco &>/dev/null; then
      echo "ERROR: choco not found — cannot auto-install VS Build Tools" >&2
      echo "Searched: ${vswhere_candidates[*]}" >&2
      echo "Listing C:/Program Files (x86)/Microsoft Visual Studio (if present):" >&2
      ls "C:/Program Files (x86)/Microsoft Visual Studio/" 2>/dev/null || echo "  (not found)" >&2
      exit 1
    fi
    choco install visualstudio2022buildtools \
      --package-parameters "--add Microsoft.VisualStudio.Workload.VCTools --includeRecommended --passive --wait" \
      -y --no-progress
    # Re-locate vswhere after installation
    for candidate in "${vswhere_candidates[@]}"; do
      if [[ -f "$candidate" ]]; then
        vswhere="$candidate"
        break
      fi
    done
    if [[ -z "${vswhere:-}" || ! -f "$vswhere" ]]; then
      echo "ERROR: vswhere.exe still not found after VS Build Tools install" >&2
      exit 1
    fi
  fi
  echo "Found vswhere.exe: $vswhere"

  local vs_path
  vs_path=$("$vswhere" -latest -products '*' -property installationPath 2>/dev/null | tr -d '\r')
  [[ -n "$vs_path" ]] || { echo "ERROR: No Visual Studio installation found" >&2; exit 1; }

  local vcvarsall="${vs_path}/VC/Auxiliary/Build/vcvarsall.bat"
  [[ -f "$vcvarsall" ]] || { echo "ERROR: vcvarsall.bat not found at: $vcvarsall" >&2; exit 1; }
  echo "Initializing MSVC: $VC_ARCH"

  # Capture env exported by vcvarsall.  Handle PATH specially: convert each
  # Windows path segment to Unix style so bash can find the MSVC executables.
  # Skip env vars whose names aren't valid bash identifiers (e.g. ProgramFiles(x86))
  # — otherwise `export` fails and set -e exits the loop before PATH is updated.
  while IFS= read -r line; do
    [[ "$line" == *=* ]] || continue
    local key="${line%%=*}"
    local val="${line#*=}"
    [[ -z "$key" ]] && continue
    # Skip invalid bash identifier names
    [[ "$key" =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ ]] || continue
    if [[ "${key,,}" == path ]]; then
      while IFS=';' read -ra segs; do
        for seg in "${segs[@]}"; do
          [[ -z "$seg" ]] && continue
          local unix_seg
          unix_seg=$(cygpath -u "$seg" 2>/dev/null || echo "$seg")
          case ":$PATH:" in *":$unix_seg:"*) ;; *) export PATH="$unix_seg:$PATH" ;; esac
        done
      done <<< "$val"
    else
      export "${key}=${val}" 2>/dev/null || true
    fi
  done < <(cmd.exe //c "\"${vcvarsall}\" ${VC_ARCH} > NUL 2>&1 && set")

  command -v cl.exe &>/dev/null \
    || { echo "ERROR: cl.exe not found after MSVC setup — check Build Tools are installed" >&2; exit 1; }
  echo "cl.exe: $(command -v cl.exe)"
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
    local vs_path
    vs_path=$("$vswhere" -latest -products '*' -property installationPath 2>/dev/null | tr -d '\r')
    if [[ -n "$vs_path" ]]; then
      local syswow64="/c/Windows/SysWOW64"
      for crt_dir in "${vs_path}"/VC/Redist/MSVC/*/x86/Microsoft.VC*.CRT; do
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

# ── Main ──────────────────────────────────────────────────────────────────────
# setup_msvc must run before setup_vcredist_x86 and setup_python so that:
#   - vswhere is available for setup_vcredist_x86 to copy x86 CRT DLLs into SysWOW64
#   - the 32-bit CRT DLLs are present before uv inspects the x86 Python interpreter
setup_env
setup_msvc
setup_vcredist_x86
setup_python
setup_rust
build_wheel
repair_wheel
finalize
test_wheel

if command -v sccache &>/dev/null; then
  sccache --show-stats || true
fi
