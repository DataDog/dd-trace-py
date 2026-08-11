#!/usr/bin/env bash
set -ex -o pipefail

# Script to determine the baseline version to compare against for a given CI run
# The results are written as environment variables to a `baseline.env` file
#
# The script will determine the baseline version (most recent tag)
#
# Env variables written to `baseline.env`:
#   - BASELINE_BRANCH: The branch or tag name of the baseline version
#   - BASELINE_COMMIT_SHA: The commit SHA of the baseline version
#   - BASELINE_COMMIT_DATE: The commit date of the baseline version
#   - BASELINE_TAG: The tag name of the baseline version (may be empty)


# The branch or tag name of the CI run
UPSTREAM_BRANCH=${UPSTREAM_BRANCH:-$CI_COMMIT_REF_NAME}

# Tags aren't branch-scoped, but make sure this checkout actually has all of
# them before resolving against them.
git fetch origin --tags --force

# See scripts/resolve_previous_version.py for why this can't be done via git
# ancestry.
resolve_tag() {
  git tag -l | scripts/resolve_previous_version.py "$1"
}

# Every branch below except the feature/merge-queue one resolves an actual
# release tag; if resolve_tag comes back empty there it's a real failure (no
# earlier final release exists), not something to paper over with the
# ancestry-based merge-base fallback used for feature branches below.
USES_TAG_BASELINE=true

# If this is a build on the `main` branch then test against the latest released version
if [ "${UPSTREAM_BRANCH}" == "main" ]; then
  echo "BASELINE_BRANCH=main" | tee baseline.env
  BASELINE_TAG=$(resolve_tag main)

# If this is a release tag (e.g. `v4.13.0rc1`) then test against the latest final
# release of the prior minor (e.g. v4.12.0) - every state of an unreleased minor
# line (branch push, rc, or the eventual final tag) anchors to the same baseline,
# so regressions can't compound silently across a chain of rc's.
elif [[ "${UPSTREAM_BRANCH}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+ ]]; then
  # BASELINE_BRANCH is purely informational here (reported below and in the
  # error message on failure) - resolve_tag works off the tag's own version
  # number, not the branch, so whether origin/${BASELINE_BRANCH} exists has no
  # bearing on the result.
  BASELINE_BRANCH=$(echo "${UPSTREAM_BRANCH:1}" | cut -d. -f1-2)
  BASELINE_TAG=$(resolve_tag "${UPSTREAM_BRANCH}")

  echo "BASELINE_BRANCH=${BASELINE_BRANCH}" | tee baseline.env

# If this is a release branch (e.g. `4.13`) then test against the same baseline
# as its tags: the latest final release of the prior minor (e.g. v4.12.0)
elif [[ "${UPSTREAM_BRANCH}" =~ ^[0-9]+\.[0-9]+$ ]]; then
  BASELINE_BRANCH="${UPSTREAM_BRANCH}"
  echo "BASELINE_BRANCH=${BASELINE_BRANCH}" | tee baseline.env
  BASELINE_TAG=$(resolve_tag "${UPSTREAM_BRANCH}")

# If this is a build on a feature branch or merge queue, then try to determine
# the base branch to compare against, defaulting to a merge-base with `main`
else
  BASELINE_BRANCH=$(.gitlab/scripts/resolve-base-branch.sh "${UPSTREAM_BRANCH}")
  echo "BASELINE_BRANCH=${BASELINE_BRANCH}" | tee baseline.env
  BASELINE_TAG=""
  USES_TAG_BASELINE=false
fi

echo "BASELINE_TAG=${BASELINE_TAG}" | tee -a baseline.env
if [ -n "${BASELINE_TAG}" ]; then
    BASELINE_COMMIT_SHA=$(git show-ref -s "${BASELINE_TAG}")
elif [ "${USES_TAG_BASELINE}" == "true" ]; then
    echo "No baseline release tag found for '${UPSTREAM_BRANCH}' (checked minor '${BASELINE_BRANCH}')." >&2
    exit 1
else
    # PR/feature/merge-queue branch: determine where it branched off from the base branch
    BASELINE_COMMIT_SHA=$(git merge-base "origin/${BASELINE_BRANCH}" "origin/${UPSTREAM_BRANCH}" || git rev-list -1 "origin/${BASELINE_BRANCH}")
fi

echo "BASELINE_COMMIT_SHA=${BASELINE_COMMIT_SHA}" | tee -a baseline.env
git checkout $BASELINE_COMMIT_SHA
echo "BASELINE_COMMIT_DATE=$(git show -s --format=%ct $BASELINE_COMMIT_SHA)" | tee -a baseline.env
