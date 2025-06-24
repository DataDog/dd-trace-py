#!/usr/bin/env bash

set -e

export K6_STATSD_ENABLE_TAGS="true"

export COMMIT_DATE=$(date -d"$CI_COMMIT_TIMESTAMP" +%s)
export CI_JOB_DATE=$(date +%s)
export CPU_MODEL=$(cat "$ARTIFACTS_DIR/lscpu.txt" | grep -Eo 'Model name: .*' | sed 's/Model name://' | awk '{$1=$1;print}')

echo ""
echo "------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------"
echo ""
echo "You can monitor macrobenchmark progress in https://ddstaging.datadoghq.com/dashboard/x79-z8u-xxh?tpl_var_bp_branch%5B0%5D=$CI_COMMIT_REF_NAME&tpl_var_bp_commit_sha%5B0%5D=$CI_COMMIT_SHORT_SHA&tpl_var_ci_pipeline_id%5B0%5D=$CI_PIPELINE_ID"
echo ""
echo "------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------"
echo ""

k6 run \
    --no-usage-report \
    --out statsd \
    --out json=/dev/stdout \
    "$REPOSITORY_ROOT/k6/script.js" | benchmark_analyzer convert --framework=k6streaming \
    --extra-params="{\
        \"cpu_model\":\"$CPU_MODEL\", \
        \"ci_job_date\":\"$CI_JOB_DATE\", \
        \"ci_job_id\":\"$CI_JOB_ID\", \
        \"ci_pipeline_id\":\"$CI_PIPELINE_ID\", \
        \"git_commit_sha\":\"$CI_COMMIT_SHA\", \
        \"git_commit_date\":\"$COMMIT_DATE\", \
        \"git_branch\":\"$CI_COMMIT_REF_NAME\"\
    }" \
    --metrics="[ \
      'agg_http_req_duration_min', \
      'agg_http_req_duration_p50', \
      'agg_http_req_duration_p75', \
      'agg_http_req_duration_p95', \
      'agg_http_req_duration_p99', \
      'agg_http_req_duration_max', \
      'throughput', 'dropped_iterations', 'data_sent', 'data_received' \
    ]" \
    --outpath="$ARTIFACTS_DIR/candidate-${EXTENDED_SCENARIO_NAME}.converted.json" /dev/stdin
