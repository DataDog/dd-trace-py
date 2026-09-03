#!/usr/bin/env bash
# Compares candidate against baseline and fails when any metric regressed by
# more than FAIL_ON_REGRESSION_THRESHOLD percent.
#
# This is the percentage-regression PR gate: unlike check-slo-breaches, which
# scores measurements against fixed SLO thresholds, this one only asks whether
# *this change* made something materially slower than the branch it merges into.
#
# See https://datadoghq.atlassian.net/wiki/spaces/APMINT/pages/5158175217/Performance+Quality+Gates
set -eo pipefail

red='\033[1;91m'
normal='\033[0m'

: "${ARTIFACTS_DIR:?ARTIFACTS_DIR must be set}"
: "${FAIL_ON_REGRESSION_THRESHOLD:?FAIL_ON_REGRESSION_THRESHOLD must be set}"
export FAIL_ON_REGRESSION_THRESHOLD

# The benchmark job writes this only when it actually ran a baseline. Without a
# baseline there is nothing to compare against, so the gate cannot conclude.
BASELINE_BRANCH=$(cat "$ARTIFACTS_DIR"/baseline_branch.txt 2>/dev/null || :)

if [ -z "$BASELINE_BRANCH" ]; then
  echo "WARN! No baseline results found, nothing to compare against."
  echo "Ensure a GitHub pull request exists for this branch if you want this check to run."
  exit 0
fi

# The label is read from the GitHub pull request, not from CI, so the bypass
# takes effect without pushing a commit.
#
# Exit code 0 means the label is present. Any non-zero exit means "do not
# bypass": no label, no pull request for this branch yet, or the lookup itself
# failed. All three must keep gating rather than silently letting a regression
# through, so the output is captured and only surfaced when it is informative.
ignore_output=$(github-should-ignore-regression \
  --for-repo="$CI_PROJECT_NAME" --for-pr="$CI_COMMIT_REF_NAME" 2>&1) && ignore_rc=0 || ignore_rc=$?

if [ "$ignore_rc" -eq 0 ]; then
  echo "WARN! This PR carries the 'performance/ignore-performance-regression' label, skipping the check."
  echo "Remove the label to re-enable it."
  exit 0
fi

if echo "$ignore_output" | grep -q "PullRequestNotFound"; then
  echo "No pull request found for $CI_COMMIT_REF_NAME, so the bypass label cannot apply. Continuing with the check."
else
  echo "Label check returned $ignore_rc (no bypass label). Continuing with the check."
fi

# A candidate file without its baseline twin means that scenario's baseline run
# never happened, so a comparison over the remaining files would silently gate
# on a subset. Fail loudly and say how to get the missing runs instead.
missing_baselines=()
for fcandidate in "$ARTIFACTS_DIR"/candidate-*.converted.json; do
  [ -e "$fcandidate" ] || continue
  fbaseline="${fcandidate/candidate-/baseline-}"
  [ -f "$fbaseline" ] || missing_baselines+=( "$fbaseline" )
done

if [ ${#missing_baselines[@]} -gt 0 ]; then
  echo -e "Please note that ${red}check failed${normal}."
  echo "Baseline benchmarks were not executed for some scenarios in this pipeline."
  echo "This usually happens when benchmark jobs ran before the pull request existed."
  echo "Missing baseline results:"
  for fbaseline in "${missing_baselines[@]}"; do
    echo " - ${fbaseline}"
  done
  echo ""
  echo "To fix, either:"
  echo "  1. Make sure the pull request exists on GitHub, re-run the 'microbenchmarks'"
  echo "     jobs that miss baseline results, then re-run this job; or"
  echo "  2. Push a new commit to $CI_COMMIT_REF_NAME, which re-runs both in the right order."
  exit 1
fi

shopt -s nullglob
reports=( "$ARTIFACTS_DIR"/baseline-*.converted.json "$ARTIFACTS_DIR"/candidate-*.converted.json )
shopt -u nullglob

if [ ${#reports[@]} -eq 0 ]; then
  echo "WARN! No converted benchmark reports found in $ARTIFACTS_DIR, nothing to check."
  exit 0
fi

# Known flaky benchmarks still run and still report their numbers, so trends
# stay visible on the dashboards; they are only dropped from the blocking set.
# Filtering happens per benchmark rather than per file because one converted
# report holds every config of a scenario, and only some configs are flaky.
if [ -n "$FLAKY_BENCHMARKS_REGEX" ]; then
  echo "Excluding known flaky benchmarks matching: $FLAKY_BENCHMARKS_REGEX"
  filtered_dir="$ARTIFACTS_DIR/regression-check"
  mkdir -p "$filtered_dir"

  filtered_reports=()
  for report in "${reports[@]}"; do
    filtered="$filtered_dir/$(basename "$report")"

    jq --arg flaky "$FLAKY_BENCHMARKS_REGEX" \
      '.benchmarks |= map(select((.parameters.scenario // "") | test($flaky) | not))' \
      "$report" > "$filtered"

    excluded=$(jq --arg flaky "$FLAKY_BENCHMARKS_REGEX" -r \
      '[.benchmarks[] | .parameters.scenario // "" | select(test($flaky))] | unique | join(" ")' \
      "$report")
    if [ -n "$excluded" ]; then
      echo "  $(basename "$report"): excluded $excluded"
    fi

    # A report whose every benchmark was flaky compares to nothing; passing an
    # empty report to the analyzer would make it error on a missing pair.
    if [ "$(jq '.benchmarks | length' "$filtered")" -gt 0 ]; then
      filtered_reports+=( "$filtered" )
    fi
  done

  # Filtering is applied to both sides independently, so a report can lose every
  # benchmark while its twin keeps some. Drop the now-unpaired survivors too,
  # rather than handing the analyzer a candidate with no baseline. Membership in
  # filtered_reports is what matters here, not the file: an emptied report was
  # still written to disk, it just carries no benchmarks.
  survivors=" ${filtered_reports[*]} "
  reports=()
  for report in "${filtered_reports[@]}"; do
    case "$(basename "$report")" in
      candidate-*) twin="${report/candidate-/baseline-}" ;;
      *)           twin="${report/baseline-/candidate-}" ;;
    esac

    if [[ "$survivors" == *" $twin "* ]]; then
      reports+=( "$report" )
    else
      echo "  $(basename "$report"): every benchmark on the other side is flaky, skipping"
    fi
  done

  if [ ${#reports[@]} -eq 0 ]; then
    echo "All benchmarks in this pipeline are marked flaky, nothing left to check."
    exit 0
  fi
fi

benchmark_analyzer compare pairwise \
  --baseline='{"baseline_or_candidate":"baseline"}' \
  --candidate='{"baseline_or_candidate":"candidate"}' \
  --format=md-nodejs \
  --fail_on_regression \
  --outpath="$ARTIFACTS_DIR/comparison-baseline-vs-candidate.md" \
  "${reports[@]}"

echo "ALL GOOD! No regressions larger than ${FAIL_ON_REGRESSION_THRESHOLD}% detected."
