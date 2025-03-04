#!/usr/bin/env bash

set -e -o pipefail

# Release branches are not expected to have PRs on Github, so we skip creating PR comment for them.
RELEASE_BRANCHES=("main" "1.x" "2.x" "3.x")

source ${CI_PROJECT_DIR}/.gitlab/benchmarks/steps/config-benchmark-analyzer.sh

red='\033[1;91m'
normal='\033[0m'

if [ ! -z "$BASELINE_BRANCH" ]; then
  missing_baselines=()

  for fcandidate in "$REPORTS_DIR"/candidate*.converted.json; do
      [ -e "$fcandidate" ] || continue

      fbaseline=$(echo "$fcandidate" | sed "s#candidate#baseline#g")

      if [[ ! -f "$fbaseline" ]]; then
        missing_baselines+=( $fbaseline )
      fi
  done

  if [ ${#missing_baselines[@]} -gt 0 ]; then
      echo "!!!!!!!!"
      echo -e " "
      echo -e "Please note that ${red}PR comment was not created${normal}."
      echo -e " "
      echo "Baseline benchmarks were not executed for some variants in this pipeline!"
      echo "This happens when you created PR, but some benchmark runs for your branch were already completed."
      echo "Missing following baseline results:"

      for fbaseline in ${missing_baselines[@]}; do
        echo " - ${fbaseline}"
      done

      echo -e " "
      echo "In order to trigger creation of PR comment, please follow the next steps:"
      echo -e " "
      echo "1. Make sure PR exists on Github."
      echo "2. Restart all benchmark jobs that miss baseline results."
      echo "3. Restart this pr-comment job."
      echo -e " "
      echo "Or you may follow alternative steps:"
      echo -e " "
      echo "1. Make sure PR exists Github."
      echo "2. Add commit to your branch $UPSTREAM_BRANCH, then push to Github."
      echo -e " "
      echo "   This way will work also if your branch is behind"
      echo "   the latest master and you rebase / merge master into your branch."
      echo -e " "
      echo "!!!!!!!!"

      exit 1
  fi

  benchmark_analyzer compare pairwise \
    --baseline='{"baseline_or_candidate":"baseline"}' \
    --candidate='{"baseline_or_candidate":"candidate"}' \
    --format=md-nodejs \
    --outpath="$REPORTS_DIR/comparison-baseline-vs-candidate.md" \
    $REPORTS_DIR/*.converted.json

  cat "$REPORTS_DIR/comparison-baseline-vs-candidate.md" | pr-commenter --for-repo="$UPSTREAM_PROJECT_NAME" --for-pr="$UPSTREAM_BRANCH" --header="Benchmarks" --on-duplicate=replace
else
  if [[ " $RELEASE_BRANCHES " =~ " $UPSTREAM_BRANCH " ]]; then
    echo "!!!!!!!!"
    echo -e " "
    echo -e "Please note that PR comment was not created."
    echo "It is expected behavior for release branch $UPSTREAM_BRANCH to not have PR comments on Github."
    echo "!!!!!!!!"
  else
    echo "!!!!!!!!"
    echo -e " "
    echo -e "Please note that ${red}PR comment was not created${normal}."
    echo "PR for the branch $UPSTREAM_BRANCH on Github doesn't exist at this point of time!"
    echo -e " "
    echo "In order to trigger creation of PR comment, please follow the next steps:"
    echo -e " "
    echo "1. Create PR on Github."
    echo "2. Restart all benchmark jobs in this pipeline."
    echo "3. Restart this pr-comment job in this pipeline."
    echo -e " "
    echo "Or you may follow alternative steps:"
    echo -e " "
    echo "1. Create PR on Github."
    echo "2. Add commit to your branch $UPSTREAM_BRANCH, then push to Github."
    echo -e " "
    echo "   This way will work also if your branch is behind"
    echo "   the latest master and you rebase / merge master into your branch."
    echo -e " "
    echo "!!!!!!!!"
  fi
fi
