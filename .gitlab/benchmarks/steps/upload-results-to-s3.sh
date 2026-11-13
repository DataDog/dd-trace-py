#!/usr/bin/env bash
set -eo pipefail

PROJECT="${UPSTREAM_PROJECT_NAME:-$CI_PROJECT_NAME}"
BRANCH="${UPSTREAM_BRANCH:-$CI_COMMIT_REF_NAME}"

# BP_EXTERNAL_S3_URL overrides the default destination prefix (must stay within
# relenv-benchmarking-data) so an external orchestrator can collect results
# under a prefix it controls.
#
# The benchmark trigger job forwards this explicitly as
# `BP_EXTERNAL_S3_URL: $BP_EXTERNAL_S3_URL` instead of `forward: pipeline_variables`
# (which would forward every parent variable and let a caller override sensitive
# child settings). When it is unset upstream, GitLab forwards the unexpanded
# literal "$BP_EXTERNAL_S3_URL" rather than an empty value, so treat that as unset.
if [[ "$BP_EXTERNAL_S3_URL" == '$BP_EXTERNAL_S3_URL' ]]; then
  BP_EXTERNAL_S3_URL=""
fi

S3_URL="${BP_EXTERNAL_S3_URL:-s3://relenv-benchmarking-data/${PROJECT}/${BRANCH}/${CI_JOB_ID}-${SCENARIO}/}"
[[ "$S3_URL" == s3://relenv-benchmarking-data/* ]] || { echo "BP_EXTERNAL_S3_URL must stay within relenv-benchmarking-data" >&2; exit 1; }

# NOTE: Converted reports go to $REPORTS_DIR rather than to /artifacts/, thus
# we need a second `s3 cp` call. The necessity of two separate dirs
# ($REPORTS_DIR, which gets resolved to /reports/, and /artifacts/) is unclear,
# therefore we keep them instead of merging them into a single (perhaps, /artifacts/) dir
aws s3 cp --recursive --acl bucket-owner-full-control "/artifacts/${CI_JOB_ID}-${SCENARIO}/" "$S3_URL"

# analyze-results.sh writes baseline/candidate.converted.json plus identical
# scenario-suffixed copies. Upload only the suffixed pair: they carry the same
# data, so uploading both would make the collector ingest each measurement twice,
# and the unique scenario-suffixed names survive if the files are ever flattened
# into one dir (scenario is also embedded in each file's parameters, so analysis
# does not depend on the name).
aws s3 cp --recursive --acl bucket-owner-full-control --exclude '*' --include "baseline-${SCENARIO}.converted.json" --include "candidate-${SCENARIO}.converted.json" "$REPORTS_DIR" "${S3_URL}converted/${SCENARIO}/"
