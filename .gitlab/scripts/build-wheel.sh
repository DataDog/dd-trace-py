#!/usr/bin/env bash
set -euo pipefail

# Helper functions for GitLab CI collapsible sections
section_start() {
    echo -e "\e[0Ksection_start:`date +%s`:$1\r\e[0K$2"
}

section_end() {
    echo -e "\e[0Ksection_end:`date +%s`:$1\r\e[0K"
}

# Setup directories
section_start "setup_env" "Setup environment"
PROJECT_DIR="${CI_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
BUILT_WHEEL_DIR="/tmp/cibuildwheel/built_wheel"
TMP_WHEEL_DIR="/tmp/cibuildwheel/tmp_wheel"
FINAL_WHEEL_DIR="${PROJECT_DIR}/pywheels"
DEBUG_WHEEL_DIR="${PROJECT_DIR}/debugwheelhouse"
mkdir -p "${BUILT_WHEEL_DIR}" "${TMP_WHEEL_DIR}" "${FINAL_WHEEL_DIR}" "${DEBUG_WHEEL_DIR}"
section_end "setup_env"

# Setup Python (verify/install uv if needed)
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
section_end "setup_python"

# Setup Rust (verify/install if needed)
section_start "install_rust" "Rust toolchain"
export PATH="${CARGO_HOME:-$HOME/.cargo}/bin:${PATH}"
if ! command -v rustc &> /dev/null; then
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
fi
rustup default stable
which rustc && rustc --version
section_end "install_rust"

# Setup sccache (verify/install if needed)
section_start "install_sccache" "sccache"
if ! command -v sccache &> /dev/null; then
    # Install openssl-devel for building sccache
    if command -v yum &> /dev/null; then
        yum install -y openssl-devel
    elif command -v apk &> /dev/null; then
        apk --no-cache add openssl-dev openssl-libs-static
    fi
    cargo install sccache
fi
which sccache && sccache --version && sccache --show-stats
section_end "install_sccache"

# Build wheel
section_start "build_wheel" "Building wheel"
uv build --wheel --out-dir "${BUILT_WHEEL_DIR}" .
BUILT_WHEEL_FILE=$(ls ${BUILT_WHEEL_DIR}/*.whl | head -n 1)
section_end "build_wheel"

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

# Finalize
section_start "finalize_wheel" "Finalizing wheel"
TMP_WHEEL_FILE=$(ls ${TMP_WHEEL_DIR}/*.whl | head -n 1)
mv "${TMP_WHEEL_FILE}" "${FINAL_WHEEL_DIR}/"
FINAL_WHEEL_FILE=$(ls ${FINAL_WHEEL_DIR}/*.whl | head -n 1)
section_end "finalize_wheel"

# Test wheel
section_start "test_wheel" "Testing wheel"
TEST_WHEEL_DIR="/tmp/test_wheel/"
mkdir -p "${TEST_WHEEL_DIR}"
uv venv "${TEST_WHEEL_DIR}/venv"
source "${TEST_WHEEL_DIR}/venv/bin/activate"
uv pip install "${FINAL_WHEEL_FILE}"
cd "${TEST_WHEEL_DIR}" && python "${PROJECT_DIR}/tests/smoke_test.py"
section_end "test_wheel"

# Teardown
section_start "teardown" "Teardown"
sccache --show-stats
section_end "teardown"
