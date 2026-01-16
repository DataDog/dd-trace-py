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
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
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
    curl -LsSf https://astral.sh/uv/install.sh | sh
  fi
  which python && python --version
  if [[ ${UNPIN_DEPENDENCIES:-"true"} == "true" ]]
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
  if [[ "${UV_PYTHON}" =~ ([0-9]+\.[0-9]+) ]]; then
    PYTHON_VER="${BASH_REMATCH[1]}"
  else
    PYTHON_VER="unknown"
  fi

  export BUILD_LOG="${DEBUG_WHEEL_DIR}/build_${PYTHON_VER}.log"
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

setup() {
  setup_env
  setup_python
  setup_rust
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
