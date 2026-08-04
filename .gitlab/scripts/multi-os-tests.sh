#!/bin/bash
# Multi-OS test script for Unix-like systems (Linux/macOS)
set -eo pipefail

curl_gitlab_api() {
  local description="$1"
  local output_file="$2"
  local headers_file="$3"
  local url="$4"
  local token_header
  local status
  local curl_exit

  for token_header in JOB-TOKEN PRIVATE-TOKEN; do
    curl_exit=0
    status=$(curl --location --silent --show-error --globoff \
      --header "$token_header: $CI_JOB_TOKEN" \
      --dump-header "$headers_file" \
      --output "$output_file" \
      --write-out "%{http_code}" \
      "$url") || curl_exit=$?

    if [[ "$curl_exit" -eq 0 && "$status" == 2?? ]]; then
      return 0
    fi

    if [[ "$curl_exit" -ne 0 ]]; then
      echo "GitLab API request for $description failed with curl exit $curl_exit using $token_header."
    else
      echo "GitLab API request for $description returned HTTP $status using $token_header."
    fi

    if [[ "$curl_exit" -eq 0 && "$status" != "403" && "$status" != "404" ]]; then
      break
    fi
  done

  echo "ERROR: Failed to fetch $description from GitLab API."
  exit 1
}

download_macos_arm64_wheels() {
  local pywheels_dir="$CI_PROJECT_DIR/pywheels"
  local artifacts_dir="$TMPDIR/gitlab-artifacts"
  local page="1"
  local next_page=""
  local job_ids=()
  local required_variable

  for required_variable in CI_JOB_TOKEN CI_API_V4_URL CI_PROJECT_ID CI_PIPELINE_ID; do
    if [[ -z "${!required_variable:-}" ]]; then
      echo "ERROR: $pywheels_dir does not exist and $required_variable is not set; cannot download macOS wheel artifacts."
      exit 1
    fi
  done

  echo "pywheels directory is missing; downloading artifacts from successful build macos arm64 jobs in pipeline $CI_PIPELINE_ID..."
  mkdir -p "$artifacts_dir"

  while true; do
    local jobs_response="$artifacts_dir/jobs-page-${page}.json"
    local jobs_headers="$artifacts_dir/jobs-page-${page}.headers"
    local page_job_ids="$artifacts_dir/job-ids-page-${page}.txt"

    curl_gitlab_api \
      "jobs for pipeline $CI_PIPELINE_ID page $page" \
      "$jobs_response" \
      "$jobs_headers" \
      "$CI_API_V4_URL/projects/$CI_PROJECT_ID/pipelines/$CI_PIPELINE_ID/jobs?per_page=100&page=$page&include_retried=true"

    if ! "$PYTHON_BIN" - "$jobs_response" > "$page_job_ids" <<'PY'; then
import json
import sys

with open(sys.argv[1]) as fp:
    jobs = json.load(fp)

if not isinstance(jobs, list):
    raise SystemExit("GitLab jobs API returned an unexpected response")

for job in jobs:
    name = job.get("name", "")
    if name.startswith("build macos arm64") and job.get("status") == "success":
        print(job["id"])
PY
      echo "ERROR: Failed to parse GitLab jobs response from $jobs_response"
      exit 1
    fi

    while IFS= read -r job_id; do
      if [[ -n "$job_id" ]]; then
        job_ids+=("$job_id")
      fi
    done < "$page_job_ids"

    next_page=$(tr -d '\r' < "$jobs_headers" | awk -F': ' 'tolower($1) == "x-next-page" {print $2}' | tail -n 1)
    if [[ -z "$next_page" ]]; then
      break
    fi
    page="$next_page"
  done

  if [[ "${#job_ids[@]}" -eq 0 ]]; then
    echo "ERROR: No successful build macos arm64 jobs found in pipeline $CI_PIPELINE_ID; cannot download macOS wheel artifacts."
    exit 1
  fi

  mkdir -p "$pywheels_dir"
  for job_id in "${job_ids[@]}"; do
    local artifact_zip="$artifacts_dir/build-macos-arm64-${job_id}.zip"
    local artifact_headers="$artifacts_dir/build-macos-arm64-${job_id}.headers"

    echo "Downloading artifacts for build macos arm64 job $job_id..."
    curl_gitlab_api \
      "artifacts for build macos arm64 job $job_id" \
      "$artifact_zip" \
      "$artifact_headers" \
      "$CI_API_V4_URL/projects/$CI_PROJECT_ID/jobs/$job_id/artifacts"

    if ! unzip -o -qq "$artifact_zip" "pywheels/*" -d "$CI_PROJECT_DIR"; then
      echo "ERROR: Failed to extract pywheels/ from artifacts for build macos arm64 job $job_id."
      exit 1
    fi
  done
}

# Install uv (retry up to 3 times)
for i in 1 2 3; do
  curl -LsSf https://astral.sh/uv/install.sh | sh && break
  echo "uv install attempt $i failed, retrying..."
  sleep 5
  [ "$i" -eq 3 ] && { echo "Failed to install uv after 3 attempts"; exit 1; }
done
export PATH="$HOME/.local/bin:$PATH"

# Create temp directory and install in isolation
export TMPDIR=$(mktemp -d)
cd "$TMPDIR"
export WHEEL_TAG="cp${PYTHON_VERSION//./}"
echo "Installing Python $PYTHON_VERSION and dependencies in $TMPDIR..."
uv python install $PYTHON_VERSION
uv venv --python $PYTHON_VERSION .venv
PYTHON_BIN="$TMPDIR/.venv/bin/python"

if [[ ! -d "$CI_PROJECT_DIR/pywheels" && "${PLATFORM:-}" == "macOS" ]]; then
  # AIDEV-NOTE: Tart VM jobs do not have gitlab-runner, so needs: artifacts are fetched manually.
  download_macos_arm64_wheels
fi

shopt -s nullglob
WHEEL_PATHS=("$CI_PROJECT_DIR"/pywheels/ddtrace*"${WHEEL_TAG}"*${WHEEL_PATTERN})
shopt -u nullglob

if [[ "${#WHEEL_PATHS[@]}" -eq 0 ]]; then
  echo "ERROR: No wheel found matching $CI_PROJECT_DIR/pywheels/ddtrace*${WHEEL_TAG}*${WHEEL_PATTERN}"
  if [[ -d "$CI_PROJECT_DIR/pywheels" ]]; then
    echo "Contents of $CI_PROJECT_DIR/pywheels:"
    find "$CI_PROJECT_DIR/pywheels" -type f | sort
  else
    echo "Directory $CI_PROJECT_DIR/pywheels does not exist."
  fi
  exit 1
fi

uv pip install --python "$PYTHON_VERSION" -r "$CI_PROJECT_DIR/.gitlab/requirements/multi-os-tests.txt" "${WHEEL_PATHS[@]}"

# Run tests
export PATH="$HOME/.local/bin:$PATH"
cd "$TMPDIR"
source .venv/bin/activate
echo "Running tests on $PLATFORM with Python $PYTHON_VERSION"
python -m pytest "$CI_PROJECT_DIR/tests/internal/service_name/test_extra_services_names.py" -v -s
python -m pytest "$CI_PROJECT_DIR/tests/appsec/architectures/test_appsec_loading_modules.py" -v -s
