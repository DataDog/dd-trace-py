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

# If this is a build on the `main` branch then test against the latest released version
if [ "${UPSTREAM_BRANCH}" == "main" ]; then
  echo "BASELINE_BRANCH=main" | tee baseline.env

  PYPI_VERSION=$(curl https://pypi.org/pypi/ddtrace/json | jq -r .info.version)
  if [[ "${PYPI_VERSION}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    BASELINE_TAG="v${PYPI_VERSION}"
  else
    BASELINE_TAG=$(git describe --tags --abbrev=0 --exclude "*rc*" "origin/main" || echo "")
  fi


# If this is a release tag (e.g. `v2.21.3`) then test against the latest version from that point (e.g. v2.21.2, or v2.20.x)
elif [[ "${UPSTREAM_BRANCH}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+ ]]; then
  # Baseline branch is the major.minor version of the tag
  BASELINE_BRANCH=$(echo "${UPSTREAM_BRANCH:1}" | cut -d. -f1-2)

  # Check if a release branch exists or not
  if git ls-remote --exit-code --heads origin "${BASELINE_BRANCH}" > /dev/null; then
    echo "Found remote branch origin/${BASELINE_BRANCH}"
  else
    echo "Remote branch origin/${BASELINE_BRANCH} not found. Falling back to main."
    BASELINE_BRANCH="main"
  fi
  # Don't forget to omit the current tag we are testing
  BASELINE_TAG=$(git describe --tags --abbrev=0 --exclude "*rc*" --exclude "${UPSTREAM_BRANCH}" "origin/${BASELINE_BRANCH}" || echo "")

  echo "BASELINE_BRANCH=${BASELINE_BRANCH}" | tee baseline.env

# If this is a release branch (e.g. `2.21`) then test against the latest version from that point (e.g. v2.21.2 or v2.20.x)
elif [[ "${UPSTREAM_BRANCH}" =~ ^[0-9]+\.[0-9]+$ ]]; then
  BASELINE_BRANCH="${UPSTREAM_BRANCH}"
  echo "BASELINE_BRANCH=${BASELINE_BRANCH}" | tee baseline.env
  BASELINE_TAG=$(git describe --tags --abbrev=0 --exclude "*rc*" "origin/${BASELINE_BRANCH}" || echo "")

# If this is a build on a feature branch, then try to detemine the base branch to compare against, default to `main`
else
  BASELINE_BRANCH=$(gh pr view --json "baseRefName" --jq ".baseRefName" "${UPSTREAM_BRANCH}" || echo "main")
  echo "BASELINE_BRANCH=${BASELINE_BRANCH}" | tee baseline.env
  BASELINE_TAG=""
fi

echo "BASELINE_TAG=${BASELINE_TAG}" | tee -a baseline.env
if [ -n "${BASELINE_TAG}" ]; then
    BASELINE_COMMIT_SHA=$(git show-ref -s "${BASELINE_TAG}")
else
    # If this is a PR/feature branch we need to determine where it has branched off from the base branch
    BASELINE_COMMIT_SHA=$(git merge-base "origin/${BASELINE_BRANCH}" "origin/${UPSTREAM_BRANCH}" || git rev-list -1 "origin/${BASELINE_BRANCH}")
fi

echo "BASELINE_COMMIT_SHA=${BASELINE_COMMIT_SHA}" | tee -a baseline.env
git checkout $BASELINE_COMMIT_SHA
echo "BASELINE_COMMIT_DATE=$(git show -s --format=%ct $BASELINE_COMMIT_SHA)" | tee -a baseline.env
