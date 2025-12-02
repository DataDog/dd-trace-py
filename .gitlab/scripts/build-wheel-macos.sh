#!/usr/bin/env bash
set -euo pipefail

echo -e "\e[0Ksection_start:`date +%s`:setup_env\r\e[0KSetup environment"
PROJECT_DIR="${CI_PROJECT_DIR:-}"
if [ -z "${PROJECT_DIR}" ]; then
  # DEV: This scripts dir but up two levels
  PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fi

BUILT_WHEEL_DIR="/tmp/cibuildwheel/built_wheel"
TMP_WHEEL_DIR="/tmp/cibuildwheel/tmp_wheel"
FINAL_WHEEL_DIR="${PROJECT_DIR}/pywheels"
DEBUG_WHEEL_DIR="${PROJECT_DIR}/debugwheelhouse"
mkdir -p "${BUILT_WHEEL_DIR}"
mkdir -p "${TMP_WHEEL_DIR}"
mkdir -p "${FINAL_WHEEL_DIR}"
mkdir -p "${DEBUG_WHEEL_DIR}"
echo -e "\e[0Ksection_end:`date +%s`:setup_env\r\e[0K"

# Setup Python environment
echo -e "\e[0Ksection_start:`date +%s`:setup_python\r\e[0KSetting up Python ${UV_PYTHON}"
export PATH="${UV_INSTALL_DIR}:${HOME}/.local/bin:${PATH}"
if ! command -v uv &> /dev/null; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
echo -e "\e[0Ksection_end:`date +%s`:setup_python\r\e[0K"

echo -e "\e[0Ksection_start:`date +%s`:install_rust\r\e[0KInstalling Rust toolchain"
export PATH="${CARGO_HOME:-$HOME/.cargo}/bin:${PATH}"
rustup update stable
rustup default stable
rustup target add aarch64-apple-darwin
which rustc
rustc --version
echo -e "\e[0Ksection_end:`date +%s`:install_rust\r\e[0K"

# Install sccache via cargo
echo -e "\e[0Ksection_start:`date +%s`:install_sccache\r\e[0KInstalling sccache"
if ! command -v sccache &> /dev/null; then
  cargo install sccache
fi
which sccache
sccache --version
sccache --show-stats
echo -e "\e[0Ksection_end:`date +%s`:install_sccache\r\e[0K"

# Build wheel
echo -e "\e[0Ksection_start:`date +%s`:build_wheel\r\e[0KBuilding wheel"
uv build --wheel --out-dir "${BUILT_WHEEL_DIR}" .
BUILT_WHEEL_FILE=$(ls ${BUILT_WHEEL_DIR}/*.whl | head -n 1)
echo -e "\e[0Ksection_end:`date +%s`:build_wheel\r\e[0K"


# Extract debug symbols
echo -e "\e[0Ksection_start:`date +%s`:extract_debug_symbols\r\e[0KExtracting debug symbols"
uv run scripts/extract_debug_symbols.py "${BUILT_WHEEL_FILE}" --output-dir "${DEBUG_WHEEL_DIR}"
echo -e "\e[0Ksection_end:`date +%s`:extract_debug_symbols\r\e[0K"

# Strip unneeded files from wheel
echo -e "\e[0Ksection_start:`date +%s`:strip_wheel\r\e[0KStripping unneeded files from wheel"
uv run scripts/zip_filter.py "${BUILT_WHEEL_FILE}" \*.c \*.cpp \*.cc \*.h \*.hpp \*.pyx \*.md
echo -e "\e[0Ksection_end:`date +%s`:strip_wheel\r\e[0K"

# List all .so files in the wheel for verification
echo -e "\e[0Ksection_start:`date +%s`:list_so_files\r\e[0KListing .so files in the wheel"
unzip -l "${BUILT_WHEEL_FILE}" | grep '\.so$'
echo -e "\e[0Ksection_end:`date +%s`:list_so_files\r\e[0K"

# Repair the wheel
echo -e "\e[0Ksection_start:`date +%s`:repair_wheel\r\e[0KRepairing wheel with delocate-wheel"
MACOSX_DEPLOYMENT_TARGET=14.0 uvx --from="delocate" delocate-wheel --require-archs "${ARCH_TAG}" -w "${TMP_WHEEL_DIR}" -v "${BUILT_WHEEL_FILE}"
echo -e "\e[0Ksection_end:`date +%s`:repair_wheel\r\e[0K"

# Move to final resting place
echo -e "\e[0Ksection_start:`date +%s`:finalize_wheel\r\e[0KFinalizing wheel"
TMP_WHEEL_FILE=$(ls ${TMP_WHEEL_DIR}/*.whl | head -n 1)
mv "${TMP_WHEEL_FILE}" "${FINAL_WHEEL_DIR}/"
FINAL_WHEEL_FILE=$(ls ${FINAL_WHEEL_DIR}/*.whl | head -n 1)
echo -e "\e[0Ksection_end:`date +%s`:finalize_wheel\r\e[0K"

# Test the wheel
echo -e "\e[0Ksection_start:`date +%s`:test_wheel\r\e[0KTesting the wheel"
TEST_WHEEL_DIR="/tmp/test_wheel/"
mkdir -p "${TEST_WHEEL_DIR}"
uv venv "${TEST_WHEEL_DIR}/venv"
# Install the package
"${TEST_WHEEL_DIR}/venv/bin/pip" install "${FINAL_WHEEL_FILE}"
# Run the smoke tests
cd "${TEST_WHEEL_DIR}" && "${TEST_WHEEL_DIR}/venv/bin/python" "${PROJECT_DIR}/tests/smoke_test.py"
echo -e "\e[0Ksection_end:`date +%s`:test_wheel\r\e[0K"

echo -e "\e[0Ksection_start:`date +%s`:teardown\r\e[0KTearing down"
sccache --show-stats
echo -e "\e[0Ksection_end:`date +%s`:teardown\r\e[0K"
