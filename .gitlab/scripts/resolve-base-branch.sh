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
# Requires `git` and, for the feature/PR-branch fallback, an authenticated
# `gh` CLI (GH_TOKEN exported or `gh auth login` already done by the caller).

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

else
  log "Ref looks like a feature/PR branch; asking GitHub for its PR base branch via 'gh pr view'"
  if BASE_BRANCH="$(gh pr view --json baseRefName --jq ".baseRefName" "${REF}" 2>&1)" && [ -n "${BASE_BRANCH}" ]; then
    log "GitHub reports PR base branch: '${BASE_BRANCH}'"
  else
    log "Could not resolve a PR base branch for '${REF}' (gh output: ${BASE_BRANCH:-<empty>}); falling back to main"
    BASE_BRANCH="main"
  fi
fi

log "Resolved base branch: '${BASE_BRANCH}'"
echo "${BASE_BRANCH}"
