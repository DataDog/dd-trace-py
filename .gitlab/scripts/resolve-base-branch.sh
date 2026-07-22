#!/usr/bin/env bash
# Resolve the base branch a CI ref should be compared against, so jobs
# (circular-import analysis, benchmark baselines) don't hardcode `main` and get
# the wrong answer for PRs targeting a release branch.
#
# Usage: resolve-base-branch.sh [ref]   (ref defaults to $CI_COMMIT_REF_NAME)
# Prints only the resolved branch to stdout; trace goes to stderr. Falls back to
# `main` when undeterminable.
#
# For a feature/PR branch: use the CI-provided target
# (CI_MERGE_REQUEST_TARGET_BRANCH_NAME / GITHUB_BASE_REF) if set, else query the
# GitHub API (GH_REPO, default DataDog/dd-trace-py; needs git/curl/jq). The API
# call auths lazily: GH_TOKEN if set, else a token minted via dd-octo-sts if
# present, else unauthenticated.

set -euo pipefail

REF="${1:-${CI_COMMIT_REF_NAME:-}}"

log() {
  echo "[resolve-base-branch] $*" >&2
}

if [ -z "${REF}" ]; then
  log "No ref given and CI_COMMIT_REF_NAME is unset; defaulting to main"
  echo "main"
  exit 0
fi

log "Resolving base branch for ref: '${REF}'"

if [ "${REF}" = "main" ]; then
  log "Ref is main; comparing against itself"
  BASE_BRANCH="main"

elif [[ "${REF}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+ ]]; then
  BASE_BRANCH="$(echo "${REF#v}" | cut -d. -f1-2)"
  log "Ref is a release tag; derived release branch '${BASE_BRANCH}'"
  if ! git ls-remote --exit-code --heads origin "refs/heads/${BASE_BRANCH}" > /dev/null 2>&1; then
    log "Remote branch origin/${BASE_BRANCH} does not exist; falling back to main"
    BASE_BRANCH="main"
  fi

elif [[ "${REF}" =~ ^[0-9]+\.[0-9]+$ ]]; then
  log "Ref is itself a release branch; comparing against itself"
  BASE_BRANCH="${REF}"

elif [[ "${REF}" =~ ^gh-readonly-queue/([^/]+)/ ]]; then
  BASE_BRANCH="${BASH_REMATCH[1]}"
  log "Ref is a GitHub merge-queue branch; extracted target branch '${BASE_BRANCH}'"

elif [ -n "${CI_MERGE_REQUEST_TARGET_BRANCH_NAME:-${GITHUB_BASE_REF:-}}" ]; then
  # Fast path: CI already told us the target; skip the API round-trip.
  BASE_BRANCH="${CI_MERGE_REQUEST_TARGET_BRANCH_NAME:-${GITHUB_BASE_REF}}"
  log "CI provided the PR target branch: '${BASE_BRANCH}'; using it directly"

else
  GITHUB_REPO="${GH_REPO:-DataDog/dd-trace-py}"
  GITHUB_OWNER="${GITHUB_REPO%%/*}"
  log "Ref looks like a feature/PR branch; querying the GitHub API for its PR base branch (repo: ${GITHUB_REPO})"

  # Auth lazily, only on the API path, so token issues can't break the fast paths.
  if [ -z "${GH_TOKEN:-}" ] && command -v dd-octo-sts > /dev/null 2>&1; then
    log "Minting a GitHub token via dd-octo-sts for the API lookup"
    GH_TOKEN="$(dd-octo-sts token --scope "${GITHUB_REPO}" --policy gitlab.github-access.read 2> /dev/null || true)"
  fi

  AUTH_HEADER=()
  if [ -n "${GH_TOKEN:-}" ]; then
    AUTH_HEADER=(-H "Authorization: Bearer ${GH_TOKEN}")
  fi

  API_URL="https://api.github.com/repos/${GITHUB_REPO}/pulls?head=${GITHUB_OWNER}:${REF}&state=open"
  RESPONSE="$(curl -sS "${AUTH_HEADER[@]}" -H "Accept: application/vnd.github+json" "${API_URL}" 2>&1)" || RESPONSE=""
  BASE_BRANCH="$(echo "${RESPONSE}" | jq -r '.[0].base.ref // empty' 2>/dev/null || true)"

  if [ -n "${BASE_BRANCH}" ]; then
    log "GitHub API reports PR base branch: '${BASE_BRANCH}'"
  else
    log "Could not resolve a PR base branch for '${REF}' in ${GITHUB_REPO} (API response: ${RESPONSE:-<empty>}); falling back to main"
    BASE_BRANCH="main"
  fi
fi

log "Resolved base branch: '${BASE_BRANCH}'"
echo "${BASE_BRANCH}"
