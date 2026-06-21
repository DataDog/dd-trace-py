#!/usr/bin/env bash
set -euo pipefail

# Helper functions for GitLab CI collapsible sections
section_start() {
    echo -e "\e[0Ksection_start:`date +%s`:$1\r\e[0K$2"
}

section_end() {
    echo -e "\e[0Ksection_end:`date +%s`:$1\r\e[0K"
}


# Setup Rust (verify/install if needed)
setup_rust() {
  section_start "install_rust" "Rust toolchain"
  export PATH="${CARGO_HOME:-$HOME/.cargo}/bin:${PATH}"
  if ! command -v rustc &> /dev/null; then
    for i in 1 2 3; do
      curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y && break
      echo "rustup install attempt $i failed, retrying..."
      sleep 5
      [ "$i" -eq 3 ] && { echo "Failed to install rustup after 3 attempts"; exit 1; }
    done
  fi
  rustup default stable
  which rustc && rustc --version
  section_end "install_rust"
}

# Setup Python (verify/install uv if needed)
setup_python() {
  section_start "setup_python" "Setting up Python ${UV_PYTHON}"
  # Set up PATH for uv and system tools
  export PATH="${UV_INSTALL_DIR:-$HOME/.local/bin}:${PATH}"
  # If UV_PYTHON is a full path (manylinux), add its bin directory to PATH
  if [[ "${UV_PYTHON}" == /* ]]; then
    export PATH="$(dirname "${UV_PYTHON}"):${PATH}"
  fi
  if ! command -v uv &> /dev/null; then
    for i in 1 2 3; do
      curl -LsSf https://astral.sh/uv/install.sh | sh && break
      echo "uv install attempt $i failed, retrying..."
      sleep 5
      [ "$i" -eq 3 ] && { echo "Failed to install uv after 3 attempts"; exit 1; }
    done
  fi
  which python && python --version
  if [[ ${UNPIN_DEPENDENCIES:-"false"} == "true" ]]
  then
    python3.14 scripts/allow_prerelease_dependencies.py
    export PIP_PRE=true
  fi
  section_end "setup_python"
}

# Setup directories
setup_env() {
  section_start "setup_env" "Setup environment"
  export PROJECT_DIR="${CI_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
  export WORK_DIR=$(mktemp -d)
  trap "rm -rf '${WORK_DIR}'" EXIT
  export BUILT_WHEEL_DIR="${WORK_DIR}/built_wheel"
  export TMP_WHEEL_DIR="${WORK_DIR}/tmp_wheel"
  export FINAL_WHEEL_DIR="${PROJECT_DIR}/pywheels"
  export DEBUG_WHEEL_DIR="${PROJECT_DIR}/debugwheelhouse"
  mkdir -p "${BUILT_WHEEL_DIR}" "${TMP_WHEEL_DIR}" "${FINAL_WHEEL_DIR}" "${DEBUG_WHEEL_DIR}"
  section_end "setup_env"
}


build_wheel() {
  section_start "build_wheel_function" "Building wheel function"

  # Determine Python version for log filename
  PYTHON_VER=$(uv run --no-project python -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')")

  # Get a rough Python tag for logging purposes
  #   e.g. "cp313-x86_64_pc_linux_gnu", "cp39-x86_64_pc_linux_musl", etc
  PYTHON_TAG=$(uv run --no-project python -c "import sysconfig; import sys; build_type = sysconfig.get_config_var('BUILD_GNU_TYPE'); py_ver = sysconfig.get_config_var('py_version_nodot'); print('cp' + py_ver + '-' + build_type.replace('-', '_'))")

  export BUILD_LOG="${DEBUG_WHEEL_DIR}/build_${PYTHON_TAG}.log"
  echo "Building wheel for Python ${PYTHON_VER} (log: ${BUILD_LOG})"

  # Redirect build output to log file
  if uv build --wheel --out-dir "${BUILT_WHEEL_DIR}" . > "${BUILD_LOG}" 2>&1; then
    echo "✓ Build completed successfully"
    export BUILT_WHEEL_FILE=$(ls ${BUILT_WHEEL_DIR}/*.whl | head -n 1)
  else
    echo "✗ Build failed! Dumping log:"
    cat "${BUILD_LOG}"
    section_end "build_wheel_function"
    exit 1
  fi

  section_end "build_wheel_function"
}

repair_wheel() {
  # Extract debug symbols
  section_start "extract_debug_symbols" "Extracting debug symbols"
  uv run --no-project scripts/extract_debug_symbols.py "${BUILT_WHEEL_FILE}" --output-dir "${DEBUG_WHEEL_DIR}"
  section_end "extract_debug_symbols"

  # Strip wheel
  section_start "strip_wheel" "Stripping unneeded files"
  uv run --no-project scripts/zip_filter.py "${BUILT_WHEEL_FILE}" \*.c \*.cpp \*.cc \*.h \*.hpp \*.pyx \*.md
  section_end "strip_wheel"

  # List .so files
  section_start "list_so_files" "Listing .so files"
  unzip -l "${BUILT_WHEEL_FILE}" | grep '\.so$'
  section_end "list_so_files"

  # Repair wheel (ONLY PLATFORM-SPECIFIC CODE)
  section_start "repair_wheel" "Repairing wheel"
  if [[ "$(uname -s)" == "Linux" ]]; then
    auditwheel repair -w "${TMP_WHEEL_DIR}" "${BUILT_WHEEL_FILE}"
  else
    # macOS
    MACOSX_DEPLOYMENT_TARGET=14.7 uvx --from="delocate" delocate-wheel \
        --require-archs "${ARCH_TAG}" -w "${TMP_WHEEL_DIR}" -v "${BUILT_WHEEL_FILE}"
  fi
  section_end "repair_wheel"
}

# Print the directory containing the NEWEST system libclang shared object, if one can be found.
_find_libclang_dir() {
  local hit
  # Sort candidates by their basename version (e.g. libclang.so.17 > libclang.so.7 > libclang.so.3.4),
  # independent of the directory prefix, and pick the highest.
  hit="$( { find /usr /opt -name 'libclang.so*' -o -name 'libclang.dylib*'; } 2>/dev/null \
          | awk -F/ '{print $NF"\t"$0}' | sort -V | tail -n1 | cut -f2- || true)"
  if [[ -n "${hit}" ]]; then
    dirname "${hit}"
    return 0
  fi
  return 1
}

# Install the self-contained `libclang` wheel from PyPI and, on success, echo the directory that
# holds its bundled shared library. This ships a modern libclang (>= 18) and is distro-independent,
# which sidesteps the ancient clang (3.4) on manylinux2014's CentOS base.
_install_libclang_via_pip() {
  local pybin="${UV_PYTHON:-}"
  if [[ -z "${pybin}" || "${pybin}" != /* ]]; then
    pybin="$(command -v python3 || command -v python || true)"
  fi
  [[ -n "${pybin}" ]] || return 1
  local target="${WORK_DIR:-/tmp}/libclang_pkg"
  "${pybin}" -m pip install --quiet --disable-pip-version-check --target "${target}" libclang &> /dev/null || return 1
  local dir="${target}/clang/native"
  ls "${dir}"/libclang.so* "${dir}"/libclang.dylib* &> /dev/null || return 1
  echo "${dir}"
}

# Ensure a libclang new enough for bindgen is available; libddwaf-sys runs bindgen at build time to
# generate the libddwaf FFI bindings, and the CI build images lack a usable libclang.
ensure_libclang() {
  section_start "ensure_libclang" "Ensuring libclang (for bindgen)"

  # macOS: libclang ships with the Xcode command line tools / Xcode toolchain.
  if [[ "$(uname -s)" == "Darwin" ]]; then
    if [[ -z "${LIBCLANG_PATH:-}" ]]; then
      local xcode_dir
      xcode_dir="$(xcode-select -p 2>/dev/null || true)"
      [[ -n "${xcode_dir}" ]] && export LIBCLANG_PATH="${xcode_dir}/Toolchains/XcodeDefault.xctoolchain/usr/lib"
    fi
    echo "LIBCLANG_PATH=${LIBCLANG_PATH:-<bindgen auto-detect>}"
    section_end "ensure_libclang"
    return 0
  fi

  local dir=""
  if command -v apk &> /dev/null; then
    # musllinux (Alpine) ships a modern clang natively; the PyPI wheel has no musllinux build.
    apk add --no-cache clang-dev llvm-dev || apk add --no-cache clang-libs llvm-libs || true
    dir="$(_find_libclang_dir || true)"
  else
    # glibc (manylinux / testrunner): prefer the self-contained modern libclang from PyPI; its CentOS
    # base only has clang 3.4 which is too old for bindgen.
    dir="$(_install_libclang_via_pip || true)"
    if [[ -z "${dir}" ]]; then
      # Fallbacks if pip is unavailable.
      if command -v dnf &> /dev/null; then
        dnf install -y clang-devel llvm-libs || true
      elif command -v yum &> /dev/null; then
        yum install -y clang-devel llvm-libs || true
        yum install -y llvm-toolset-7.0 || true
      fi
      dir="$(_find_libclang_dir || true)"
    fi
  fi

  if [[ -n "${dir}" ]]; then
    export LIBCLANG_PATH="${dir}"
    echo "LIBCLANG_PATH=${LIBCLANG_PATH}"
  else
    echo "WARNING: libclang not found after install attempt; bindgen (libddwaf-sys) will fail" >&2
  fi
  section_end "ensure_libclang"
}

setup() {
  setup_env
  setup_python
  setup_rust
  ensure_libclang
}

# Finalize
finalize() {
  section_start "finalize_wheel" "Finalizing wheel"
  export TMP_WHEEL_FILE=$(ls ${TMP_WHEEL_DIR}/*.whl | head -n 1)
  WHEEL_BASENAME=$(basename "${TMP_WHEEL_FILE}")
  mv "${TMP_WHEEL_FILE}" "${FINAL_WHEEL_DIR}/"
  export FINAL_WHEEL_FILE="${FINAL_WHEEL_DIR}/${WHEEL_BASENAME}"
  section_end "finalize_wheel"
}


# Test wheel
test_wheel() {
  section_start "test_wheel" "Testing wheel"
  export UV_LINK_MODE=copy
  export TEST_WHEEL_DIR="${WORK_DIR}/test_wheel"
  mkdir -p "${TEST_WHEEL_DIR}"
  export VENV_PATH="${TEST_WHEEL_DIR}/venv"
  uv venv --python="${UV_PYTHON}" "${VENV_PATH}"
  export VIRTUAL_ENV="${VENV_PATH}"
  export PATH="${VENV_PATH}/bin:${PATH}"
  cd "${TEST_WHEEL_DIR}"
  ls -al "${FINAL_WHEEL_FILE}"
  # Activate venv and install wheel in a subshell
  # Unset UV_PYTHON so uv respects the venv instead of the global setting
  (
    unset UV_PYTHON
    source "${VENV_PATH}/bin/activate"
    uv pip install "${FINAL_WHEEL_FILE}"
  )

  # Diagnostics before running smoke test
  echo "=== Environment Diagnostics ==="
  echo "VIRTUAL_ENV: ${VIRTUAL_ENV}"
  echo "PATH: ${PATH}"
  echo "which python: $(which python)"
  "${VENV_PATH}/bin/python" --version
  echo "=== pip freeze ==="
  uv pip freeze
  echo "=== site-packages contents ==="
  "${VENV_PATH}/bin/python" -c "import site; print('site-packages:', site.getsitepackages())"
  ls -la "${VENV_PATH}/lib/"*/site-packages/ | head -30
  echo "=== Testing direct import ==="
  "${VENV_PATH}/bin/python" -c "import ddtrace; print('✓ ddtrace import successful')" || echo "✗ ddtrace import failed"

  echo "=== Running smoke test ==="
  "${VENV_PATH}/bin/python" "${PROJECT_DIR}/tests/smoke_test.py"
  section_end "test_wheel"
}
