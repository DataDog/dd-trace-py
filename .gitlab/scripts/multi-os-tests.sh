#!/bin/bash
# Multi-OS test script for Unix-like systems (Linux/macOS)
set -eo pipefail

GITLAB_API_PRIVATE_TOKEN=""

ensure_aws_credentials_for_authanywhere() {
  local creds
  local role

  if [[ -n "${AWS_ACCESS_KEY_ID:-}" && -n "${AWS_SECRET_ACCESS_KEY:-}" && -n "${AWS_SESSION_TOKEN:-}" ]]; then
    return 0
  fi

  if [[ -z "${AWS_EC2_METADATA_SERVICE_ENDPOINT:-}" ]]; then
    echo "ERROR: AWS_EC2_METADATA_SERVICE_ENDPOINT is not set; authanywhere cannot use the Tart IAM proxy."
    return 1
  fi

  echo "Resolving AWS credentials from the Tart IAM proxy for authanywhere..."
  if ! role=$(curl --fail --silent --show-error "${AWS_EC2_METADATA_SERVICE_ENDPOINT}/latest/meta-data/iam/security-credentials/"); then
    echo "ERROR: Failed to resolve the Tart IAM proxy role name."
    return 1
  fi

  if ! creds=$(curl --fail --silent --show-error "${AWS_EC2_METADATA_SERVICE_ENDPOINT}/latest/meta-data/iam/security-credentials/${role}"); then
    echo "ERROR: Failed to resolve AWS credentials from the Tart IAM proxy."
    return 1
  fi

  if ! AWS_ACCESS_KEY_ID=$("$PYTHON_BIN" -c 'import json, sys; print(json.load(sys.stdin)["AccessKeyId"])' <<<"$creds"); then
    echo "ERROR: Failed to parse AWS access key from the Tart IAM proxy response."
    return 1
  fi
  if ! AWS_SECRET_ACCESS_KEY=$("$PYTHON_BIN" -c 'import json, sys; print(json.load(sys.stdin)["SecretAccessKey"])' <<<"$creds"); then
    echo "ERROR: Failed to parse AWS secret key from the Tart IAM proxy response."
    return 1
  fi
  if ! AWS_SESSION_TOKEN=$("$PYTHON_BIN" -c 'import json, sys; print(json.load(sys.stdin)["Token"])' <<<"$creds"); then
    echo "ERROR: Failed to parse AWS session token from the Tart IAM proxy response."
    return 1
  fi
  export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
}

# AIDEV-NOTE: ddbuild returns 404 for CI_JOB_TOKEN on pipeline job listing; BTI fallback needs Tart IAM credentials.
get_gitlab_api_private_token() {
  local auth_header
  local authanywhere_bin
  local authanywhere_url
  local uname_arch
  local uname_os
  local curl_exit
  local os_arch
  local os_name
  local status
  local token_headers="$TMPDIR/gitlab-artifacts/gitlab-token.headers"
  local token_response="$TMPDIR/gitlab-artifacts/gitlab-token.json"

  if [[ -n "$GITLAB_API_PRIVATE_TOKEN" ]]; then
    return 0
  fi

  authanywhere_bin="$(command -v authanywhere || true)"
  if [[ -z "$authanywhere_bin" ]]; then
    uname_os="$(uname -s)"
    uname_arch="$(uname -m)"

    case "$uname_os" in
      Darwin) os_name="darwin" ;;
      Linux) os_name="linux" ;;
      *)
        echo "ERROR: Cannot download authanywhere for unsupported OS $uname_os."
        exit 1
        ;;
    esac

    case "$uname_arch" in
      arm64|aarch64) os_arch="arm64" ;;
      x86_64|amd64) os_arch="amd64" ;;
      *)
        echo "ERROR: Cannot download authanywhere for unsupported architecture $uname_arch."
        exit 1
        ;;
    esac

    authanywhere_bin="$TMPDIR/gitlab-artifacts/authanywhere-${os_name}-${os_arch}"
    authanywhere_url="https://binaries.ddbuild.io/dd-source/authanywhere/LATEST/authanywhere-${os_name}-${os_arch}"
    echo "authanywhere is not available; downloading $authanywhere_url..."
    if ! curl --location --silent --show-error --fail --output "$authanywhere_bin" "$authanywhere_url"; then
      echo "ERROR: Failed to download authanywhere from $authanywhere_url."
      exit 1
    fi
    chmod +x "$authanywhere_bin"
  fi

  echo "GitLab API job-token auth failed; requesting a short-lived GitLab API token from BTI..."
  if ! ensure_aws_credentials_for_authanywhere; then
    exit 1
  fi
  if ! auth_header="$("$authanywhere_bin" --audience rapid-devex-ci)"; then
    echo "ERROR: Failed to get a BTI auth header from authanywhere."
    exit 1
  fi
  if [[ "$auth_header" != Authorization:* ]]; then
    echo "ERROR: authanywhere returned an unexpected auth header."
    exit 1
  fi

  curl_exit=0
  status=$(curl --location --silent --show-error \
    --header "$auth_header" \
    --dump-header "$token_headers" \
    --output "$token_response" \
    --write-out "%{http_code}" \
    "https://bti-ci-api.us1.ddbuild.io/internal/ci/gitlab/token?owner=DataDog&repository=dd-trace-py") || curl_exit=$?

  if [[ "$curl_exit" -ne 0 || "$status" != 2?? ]]; then
    echo "ERROR: Failed to request a GitLab API token from BTI; curl exit $curl_exit, HTTP $status."
    exit 1
  fi

  GITLAB_API_PRIVATE_TOKEN=$("$PYTHON_BIN" - "$token_response" <<'PY'
import json
import sys

with open(sys.argv[1]) as fp:
    print(json.load(fp)["token"])
PY
  ) || {
    echo "ERROR: Failed to parse GitLab API token response from BTI."
    exit 1
  }
}

curl_gitlab_api_once() {
  local description="$1"
  local output_file="$2"
  local headers_file="$3"
  local url="$4"
  local token_header="$5"
  local token_value="$6"
  local curl_exit
  local status

  curl_exit=0
  status=$(curl --location --silent --show-error --globoff \
    --header "$token_header: $token_value" \
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

  CURL_GITLAB_API_LAST_EXIT="$curl_exit"
  CURL_GITLAB_API_LAST_STATUS="$status"
  return 1
}

curl_gitlab_api() {
  local description="$1"
  local output_file="$2"
  local headers_file="$3"
  local url="$4"
  local token_header

  for token_header in JOB-TOKEN PRIVATE-TOKEN; do
    if curl_gitlab_api_once "$description" "$output_file" "$headers_file" "$url" "$token_header" "$CI_JOB_TOKEN"; then
      return 0
    fi

    if [[ "$CURL_GITLAB_API_LAST_EXIT" -eq 0 && ! "$CURL_GITLAB_API_LAST_STATUS" =~ ^40[134]$ ]]; then
      break
    fi
  done

  if [[ "$CURL_GITLAB_API_LAST_EXIT" -eq 0 && "$CURL_GITLAB_API_LAST_STATUS" =~ ^40[134]$ ]]; then
    get_gitlab_api_private_token
    if curl_gitlab_api_once "$description" "$output_file" "$headers_file" "$url" "PRIVATE-TOKEN" "$GITLAB_API_PRIVATE_TOKEN"; then
      return 0
    fi
  fi

  echo "ERROR: Failed to fetch $description from GitLab API."
  exit 1
}

urlencode() {
  "$PYTHON_BIN" -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$1"
}

curl_gitlab_artifacts_by_ref() {
  local ref_name="$1"
  local job_name="$2"
  local output_file="$3"
  local headers_file="$4"
  local encoded_ref
  local encoded_job
  local token_header
  local curl_exit
  local status
  local url

  encoded_ref="$(urlencode "$ref_name")"
  encoded_job="$(urlencode "$job_name")"
  url="$CI_API_V4_URL/projects/$CI_PROJECT_ID/jobs/artifacts/$encoded_ref/download?job=$encoded_job"

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
      echo "GitLab artifact-by-ref request for $job_name at $ref_name failed with curl exit $curl_exit using $token_header."
    else
      echo "GitLab artifact-by-ref request for $job_name at $ref_name returned HTTP $status using $token_header."
    fi
  done

  return 1
}

extract_pywheel_artifacts() {
  local artifact_zip="$1"
  local description="$2"

  if ! unzip -o -qq "$artifact_zip" "pywheels/*" -d "$CI_PROJECT_DIR"; then
    echo "ERROR: Failed to extract pywheels/ from $description."
    return 1
  fi
}

download_macos_arm64_wheels_by_ref() {
  local artifacts_dir="$1"
  local ref_name
  local job_name
  local artifact_zip
  local artifact_headers
  local download_ok
  local ref_names=()
  local job_names=(
    "build macos arm64: [3.9 3.10 3.11]"
    "build macos arm64: [3.12 3.13 3.14]"
  )

  ref_names+=("refs/pipelines/$CI_PIPELINE_ID")
  if [[ -n "${CI_COMMIT_REF_NAME:-}" ]]; then
    ref_names+=("$CI_COMMIT_REF_NAME")
  fi

  for ref_name in "${ref_names[@]}"; do
    download_ok=1
    echo "Trying direct artifact download for build macos arm64 jobs at ref $ref_name..."

    for job_name in "${job_names[@]}"; do
      artifact_zip="$artifacts_dir/$(echo "$job_name-$ref_name" | tr -cs '[:alnum:]' '-').zip"
      artifact_headers="${artifact_zip%.zip}.headers"

      if ! curl_gitlab_artifacts_by_ref "$ref_name" "$job_name" "$artifact_zip" "$artifact_headers"; then
        download_ok=0
        break
      fi

      if ! extract_pywheel_artifacts "$artifact_zip" "artifacts for $job_name at $ref_name"; then
        download_ok=0
        break
      fi
    done

    if [[ "$download_ok" -eq 1 ]]; then
      echo "Downloaded macOS wheel artifacts by ref $ref_name."
      return 0
    fi
  done

  return 1
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

  if download_macos_arm64_wheels_by_ref "$artifacts_dir"; then
    return 0
  fi

  echo "Direct artifact download by ref failed; falling back to GitLab Jobs API lookup..."

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

    if ! extract_pywheel_artifacts "$artifact_zip" "artifacts for build macos arm64 job $job_id"; then
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
