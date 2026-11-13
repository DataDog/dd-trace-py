#!/usr/bin/env bash
set -ex -o pipefail

ARTIFACTS_DIR="/artifacts/${CI_JOB_ID}-${SCENARIO}"
mkdir -p "${ARTIFACTS_DIR}"

# Benchmark analyzer config

source $(dirname "$0")/config-benchmark-analyzer.sh

CPU_MODEL=$(cat "$REPORTS_DIR/lscpu.txt" | grep -Eo 'Model name: .*' | sed 's/Model name://' | awk '{$1=$1;print}')
CI_JOB_DATE=$(date +%s)

if [ -z "$CPU_MODEL" ]; then
  echo "FAIL! Failed to extract CPU_MODEL from lscpu.txt"
  exit 1
fi

KERNEL_VERSION=$(uname -a || echo "Unknown")

# -- Analyze candidate branch results
CANDIDATE_RESULTS_DIR="$ARTIFACTS_DIR/candidate"

# Get commit SHA for candidate branch
if [ -z "$CANDIDATE_COMMIT_SHA" ];
then
  CANDIDATE_BRANCH=$UPSTREAM_BRANCH
  CANDIDATE_SRC="/app/candidate/"
  cd $CANDIDATE_SRC
  CANDIDATE_COMMIT_SHA=$(git rev-parse HEAD)
  CANDIDATE_COMMIT_DATE=$(git show -s --format=%ct $CANDIDATE_COMMIT_SHA)
fi

benchmark_analyzer convert \
  --extra-params="{\
  \"baseline_or_candidate\":\"candidate\", \
  \"cpu_model\":\"$CPU_MODEL\", \
  \"kernel_version\":\"$KERNEL_VERSION\", \
  \"ci_job_date\":\"$CI_JOB_DATE\", \
  \"ci_job_id\":\"$CI_JOB_ID\", \
  \"ci_pipeline_id\":\"$CI_PIPELINE_ID\", \
  \"git_commit_sha\":\"$CANDIDATE_COMMIT_SHA\", \
  \"git_commit_date\":\"$CANDIDATE_COMMIT_DATE\", \
  \"git_branch\":\"$CANDIDATE_BRANCH\"\
  }" \
  --framework=Pyperf \
  --outpath="$REPORTS_DIR/candidate.converted.json" \
  "$CANDIDATE_RESULTS_DIR/results.json"

cp "$REPORTS_DIR/candidate.converted.json" "$REPORTS_DIR/candidate-$SCENARIO.converted.json"

benchmark_analyzer analyze \
  --format=md \
  --outpath="$REPORTS_DIR/candidate-analysis.md" \
 "$REPORTS_DIR/candidate.converted.json"

# -- Analyze baseline branch results

if [[ -z "$BASELINE_COMMIT_DATE" ]];
then
  BASELINE_SRC="/app/baseline/"
  if [ -d $BASELINE_SRC ]; then
    BASELINE_BRANCH=$(github-find-merge-into-branch --for-repo="$UPSTREAM_PROJECT_NAME" --for-pr="$UPSTREAM_BRANCH" || :)

    # Get commit SHA for baseline branch
    cd $BASELINE_SRC
    BASELINE_COMMIT_SHA=$(git rev-parse HEAD)
    BASELINE_COMMIT_DATE=$(git show -s --format=%ct $BASELINE_COMMIT_SHA)
  fi
fi

if [[ -n "$BASELINE_COMMIT_DATE" ]];
then
  BASELINE_RESULTS_DIR="$ARTIFACTS_DIR/baseline"
  benchmark_analyzer convert \
    --extra-params="{\
    \"baseline_or_candidate\":\"baseline\", \
    \"cpu_model\":\"$CPU_MODEL\", \
    \"kernel_version\":\"$KERNEL_VERSION\", \
    \"ci_job_date\":\"$CI_JOB_DATE\", \
    \"ci_job_id\":\"$CI_JOB_ID\", \
    \"ci_pipeline_id\":\"$CI_PIPELINE_ID\", \
    \"git_commit_sha\":\"$BASELINE_COMMIT_SHA\", \
    \"git_commit_date\":\"$BASELINE_COMMIT_DATE\", \
    \"git_branch\":\"$BASELINE_BRANCH\"\
    }" \
    --framework=Pyperf \
    --outpath="$REPORTS_DIR/baseline.converted.json" \
    "$BASELINE_RESULTS_DIR/results.json"

  cp "$REPORTS_DIR/baseline.converted.json" "$REPORTS_DIR/baseline-$SCENARIO.converted.json"

  benchmark_analyzer analyze \
    --format=md \
    --outpath="$REPORTS_DIR/baseline-analysis.md" \
   "$REPORTS_DIR/baseline.converted.json"

  # -- Compare results between baseline and candidate branches
  benchmark_analyzer compare pairwise \
    --baseline='{"baseline_or_candidate":"baseline"}' \
    --candidate='{"baseline_or_candidate":"candidate"}' \
    --format=md-nodejs \
    --outpath="$REPORTS_DIR/comparison-baseline-vs-candidate.md" \
   "$REPORTS_DIR/baseline.converted.json" "$REPORTS_DIR/candidate.converted.json"
fi
