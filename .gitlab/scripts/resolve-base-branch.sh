#!/usr/bin/env bash
# Resolve the branch that a CI ref should be compared against (its "base" or
# "baseline" branch), so jobs that diff a PR/branch against upstream (e.g.
# circular import analysis, benchmark baselines) don't hardcode `main` and
# get the wrong answer for PRs targeting a release branch.
#
# Usage:
#   resolve-base-branch.sh [ref]
#
#   ref   Git ref/branch/tag to resolve from. Defaults to $CI_COMMIT_REF_NAME.
#
# Prints ONLY the resolved base branch name to stdout, so callers can do:
#   BASE_BRANCH=$(.gitlab/scripts/resolve-base-branch.sh)
#
# All debug/trace output goes to stderr and is visible in the job log, so if
# the wrong branch gets resolved it's clear which rule fired and why.
#
# Falls back to "main" whenever the branch can't be determined (e.g. no open
# PR for the ref, or the GitHub lookup fails), rather than erroring out.
#
# For a feature/PR branch the target is resolved in two steps:
#   1. If the CI system already exposes the PR/MR target branch
#      (CI_MERGE_REQUEST_TARGET_BRANCH_NAME on GitLab merge-request pipelines,
#      GITHUB_BASE_REF on GitHub pull_request events), use it directly. This is
#      the cheapest, most authoritative source and needs no token or network.
#   2. Otherwise (e.g. this repo's GitLab branch pipelines, where neither var is
#      set) query the GitHub REST API for the ref's open PR base branch.
#
# Requires `git`, `curl` and `jq`. The API fallback queries GitHub directly
# (rather than shelling out to the `gh` CLI, which isn't installed in every
# image this script runs in) against GH_REPO (defaults to DataDog/dd-trace-py) —
# independent of whatever the local `origin` remote happens to point at (e.g. a
# GitLab mirror). Set GH_TOKEN to avoid unauthenticated GitHub API rate limits.

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
  # Fast path: the CI system already told us the PR/MR target branch, so trust
  # it and skip the GitHub API round-trip (and its token requirement).
  BASE_BRANCH="${CI_MERGE_REQUEST_TARGET_BRANCH_NAME:-${GITHUB_BASE_REF}}"
  log "CI provided the PR target branch: '${BASE_BRANCH}'; using it directly"

else
  GITHUB_REPO="${GH_REPO:-DataDog/dd-trace-py}"
  GITHUB_OWNER="${GITHUB_REPO%%/*}"
  log "Ref looks like a feature/PR branch; querying the GitHub API for its PR base branch (repo: ${GITHUB_REPO})"

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
