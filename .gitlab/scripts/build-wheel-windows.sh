#!/usr/bin/env bash
# Build a Windows wheel for dd-trace-py using the pre-configured Docker image.
# Runs under Git Bash (bash.exe from Git for Windows) on windows-v2:2022 runners.
#
# Required env vars:
#   PYTHON_VERSION   Python version to build for (e.g. "3.12")
#   WINDOWS_ARCH     Target architecture: "amd64" or "x86" (default: "amd64")

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/build-wheel-helpers.sh"

PYTHON_VERSION=${PYTHON_VERSION:?PYTHON_VERSION is required}
WINDOWS_ARCH=${WINDOWS_ARCH:-amd64}

case "$WINDOWS_ARCH" in
    x86)   UV_PYTHON_PLATFORM="windows-x86";    VC_ARCH="x64_x86" ;;
    amd64) UV_PYTHON_PLATFORM="windows-x86_64"; VC_ARCH="amd64"   ;;
    *)     echo "ERROR: Unsupported WINDOWS_ARCH=$WINDOWS_ARCH" >&2; exit 1 ;;
esac
export UV_PYTHON="cpython-${PYTHON_VERSION}-${UV_PYTHON_PLATFORM}"

IMAGE="${WINDOWS_BUILD_IMAGE:?WINDOWS_BUILD_IMAGE is required}"

setup_env

# Canceling a job kills this script but not the container started by `docker run`.
# That container keeps building and writes its wheel into dist/ of the next job
# scheduled on this VM (same project dir), which then finds two wheels.
# Remove containers left by a previous job before building.
CONTAINER_LABEL="dd-trace-py.build-windows"
JOB_LABEL="dd-trace-py.job-id"
stale_containers=$(docker ps -aq --filter "label=${CONTAINER_LABEL}")
if [[ -n "${stale_containers}" ]]; then
    echo "Removing containers left by a previous job:"
    docker ps -a --filter "label=${CONTAINER_LABEL}" \
        --format "  {{.ID}} job={{.Label \"${JOB_LABEL}\"}} created={{.CreatedAt}} status={{.Status}}"
    # shellcheck disable=SC2086
    docker rm -f ${stale_containers}
fi

# Diagnostics: record who we are and leave a marker, so any other job using
# the same directory shows up below.
MARKER_DIR="${PROJECT_DIR}/.job-markers"
print_workspace_diagnostics() {
    echo "=== Workspace diagnostics ==="
    echo "host=$(hostname) job=${CI_JOB_ID:-?} concurrent_id=${CI_CONCURRENT_ID:-?} runner=${CI_RUNNER_ID:-?} (${CI_RUNNER_DESCRIPTION:-?})"
    echo "project_dir=${PROJECT_DIR}"
    echo "Job markers in ${MARKER_DIR} (one per job that used this directory since its last clean):"
    for marker in "${MARKER_DIR}"/*; do
        [[ -f "${marker}" ]] && echo "  $(basename "${marker}"): $(cat "${marker}")"
    done
    echo "Wheels in ${PROJECT_DIR}/dist:"
    ls -la --time-style=full-iso "${PROJECT_DIR}/dist/"*.whl 2>/dev/null || echo "  (none)"
}
mkdir -p "${MARKER_DIR}"
echo "host=$(hostname) started=$(date -u +%FT%TZ) python=${PYTHON_VERSION} arch=${WINDOWS_ARCH}" > "${MARKER_DIR}/${CI_JOB_ID:-local}"

# Git Bash path → Windows path (required for docker -v flag on Windows)
PROJECT_DIR_WIN=$(cygpath -w "${PROJECT_DIR}")

section_start "docker_pull" "Pulling Windows build image"
docker pull "${IMAGE}"
section_end "docker_pull"

section_start "build_wheel" "Building wheel (${UV_PYTHON}, ${WINDOWS_ARCH})"

docker run --rm \
  --label "${CONTAINER_LABEL}" \
  --label "${JOB_LABEL}=${CI_JOB_ID:-local}" \
  -v "${PROJECT_DIR_WIN}:C:\\workspace" \
  -e "UV_PYTHON=${UV_PYTHON}" \
  -e "UV_PYTHON_INSTALL_DIR=C:\\tools\\uv-python" \
  -w 'C:\workspace' \
  "${IMAGE}" \
  powershell -NoProfile -NonInteractive \
    -File 'C:\workspace\.gitlab\scripts\windows-docker-build.ps1' \
    -VcArch "${VC_ARCH}" || { print_workspace_diagnostics; exit 1; }
print_workspace_diagnostics

export BUILT_WHEEL_FILE
BUILT_WHEEL_FILE=$(ls "${PROJECT_DIR}/dist/"*.whl | head -n 1)
echo "Built wheel: ${BUILT_WHEEL_FILE}"
cp "${BUILT_WHEEL_FILE}" "${TMP_WHEEL_DIR}/"
section_end "build_wheel"

finalize

section_start "test_wheel" "Testing wheel (${UV_PYTHON})"
docker run --rm \
  --label "${CONTAINER_LABEL}" \
  --label "${JOB_LABEL}=${CI_JOB_ID:-local}" \
  -v "${PROJECT_DIR_WIN}:C:\\workspace" \
  -e "UV_PYTHON_INSTALL_DIR=C:\\tools\\uv-python" \
  -w 'C:\workspace' \
  "${IMAGE}" \
  powershell -NoProfile -NonInteractive \
    -File 'C:\workspace\.gitlab\scripts\windows-docker-test.ps1' \
    -UvPython "${UV_PYTHON}"
section_end "test_wheel"
