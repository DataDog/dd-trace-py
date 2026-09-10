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
  uv run --no-project scripts/extract_debug_symbols.py "${BUILT_WHEEL_FILE}" \
    --output-dir "${DEBUG_WHEEL_DIR}" \
    --ignore-patterns "libddwaf*,libdd_heap_gotter*"
  section_end "extract_debug_symbols"

  # Heap-gotter cdylib debug symbols are extracted in setup.py (build_heap_gotter);
  # merge any staged .debug sidecars into the debug-symbols package.
  section_start "merge_heap_gotter_debug_symbols" "Merging heap-gotter debug symbols"
  uv run --no-project python - <<'PY'
import glob
import os
import zipfile
from pathlib import Path

project_dir = os.environ["PROJECT_DIR"]
debug_dir = os.environ["DEBUG_WHEEL_DIR"]
sidecars = sorted(Path(project_dir, "build").rglob("libdd_heap_gotter*.debug"))
if not sidecars:
    print("No heap-gotter debug sidecars found")
    raise SystemExit(0)
packages = glob.glob(os.path.join(debug_dir, "*-debug-symbols.zip"))
if not packages:
    print("WARNING: no debug-symbols package to merge heap-gotter sidecars into")
    raise SystemExit(0)
pkg = packages[0]
with zipfile.ZipFile(pkg, "a", zipfile.ZIP_DEFLATED) as zf:
    existing = set(zf.namelist())
    for sidecar in sidecars:
        parts = sidecar.parts
        try:
            arc = str(Path(*parts[parts.index("ddtrace") :]))
        except ValueError:
            arc = sidecar.name
        if arc not in existing:
            zf.write(sidecar, arc)
            print(f"Added heap-gotter debug symbols: {arc}")
PY
  section_end "merge_heap_gotter_debug_symbols"

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
    # Heap-gotter's ELF versioning trips auditwheel iter_versions; --exclude
    # only skips SONAME grafting, so stash .so (+ .so.debug) out of the wheel,
    # repair, then reinsert the runtime .so. Python zipfile: Info-ZIP globs are
    # unreliable on archive paths.
    GOTTER_STASH_DIR="${WORK_DIR}/heap_gotter_stash"
    mkdir -p "${GOTTER_STASH_DIR}"
    BUILT_WHEEL_FILE="${BUILT_WHEEL_FILE}" GOTTER_STASH_DIR="${GOTTER_STASH_DIR}" \
      uv run --no-project python - <<'PY'
import os
import zipfile
from pathlib import Path

import subprocess
import sys

wheel = Path(os.environ["BUILT_WHEEL_FILE"])
stash = Path(os.environ["GOTTER_STASH_DIR"])
marker = "libdd_heap_gotter"
with zipfile.ZipFile(wheel, "r") as zf:
    gotter = [
        n
        for n in zf.namelist()
        if marker in Path(n).name and (n.endswith(".so") or n.endswith(".so.debug"))
    ]
    for name in gotter:
        dest = stash / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(zf.read(name))
        print(f"Stashed heap-gotter artifact: {name}")
if gotter:
    # zip_filter keeps RECORD consistent.
    patterns = [f"*{marker}*.so", f"*{marker}*.so.debug", f"*/{marker}*"]
    subprocess.check_call([sys.executable, "scripts/zip_filter.py", str(wheel), *patterns])
with zipfile.ZipFile(wheel, "r") as zf:
    leftover = [n for n in zf.namelist() if marker in Path(n).name]
if leftover:
    raise SystemExit(f"heap-gotter still in wheel before auditwheel: {leftover}")
print(f"heap-gotter stash count before auditwheel: {len(gotter)}")
PY

    auditwheel repair -w "${TMP_WHEEL_DIR}" "${BUILT_WHEEL_FILE}"

    if find "${GOTTER_STASH_DIR}" \( -name 'libdd_heap_gotter*.so' -o -name 'libdd_heap_gotter*.so.debug' \) 2>/dev/null | grep -q .; then
      REPAIRED_WHEEL_FILE=$(ls "${TMP_WHEEL_DIR}"/*.whl | head -n 1)
      GOTTER_STASH_DIR="${GOTTER_STASH_DIR}" REPAIRED_WHEEL_FILE="${REPAIRED_WHEEL_FILE}" \
        uv run --no-project python - <<'PY'
import base64
import csv
import hashlib
import io
import os
import zipfile
from pathlib import Path

wheel = Path(os.environ["REPAIRED_WHEEL_FILE"])
stash = Path(os.environ["GOTTER_STASH_DIR"])

# Runtime .so only; .so.debug stays in debugwheelhouse.
additions = {
    str(p.relative_to(stash)): p
    for p in sorted(stash.rglob("*"))
    if p.is_file() and p.name.endswith(".so")
}
if not additions:
    print("No stashed heap-gotter cdylib to reinsert")
    raise SystemExit(0)

tmp_wheel = Path(f"{wheel}.tmp")
with (
    zipfile.ZipFile(wheel, "r") as source_zip,
    zipfile.ZipFile(tmp_wheel, "w", zipfile.ZIP_DEFLATED) as temp_zip,
):
    record = next((f for f in source_zip.infolist() if f.filename.endswith(".dist-info/RECORD")), None)
    if record is None:
        raise SystemExit(f"no RECORD found in {wheel}")
    # DEV: Use ZipInfo objects to ensure original file attributes are preserved
    for file in source_zip.infolist():
        if file.filename == record.filename or file.filename in additions:
            continue
        temp_zip.writestr(file, source_zip.read(file.filename))
    rows = [r for r in csv.reader(io.StringIO(source_zip.read(record.filename).decode("utf-8"))) if r]
    rows = [r for r in rows if r[0] != record.filename and r[0] not in additions]
    for arcname, path in additions.items():
        data = path.read_bytes()
        temp_zip.writestr(arcname, data)
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")
        rows.append([arcname, f"sha256={digest}", str(len(data))])
        print(f"Reinserted heap-gotter cdylib: {arcname}")
    rows.append([record.filename, "", ""])
    output = io.StringIO()
    csv.writer(output, lineterminator="\n").writerows(rows)
    temp_zip.writestr(record, output.getvalue())
os.replace(tmp_wheel, wheel)
PY
    fi
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
