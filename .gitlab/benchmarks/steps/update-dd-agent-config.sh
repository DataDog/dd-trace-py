#!/usr/bin/env bash

set -e

export DD_HOSTNAME="$(hostname)-${SCENARIO//_/-}"
if [ -z "$DD_API_KEY" ]; then
  export DD_API_KEY=$(aws ssm get-parameter --region us-east-1 --name ci.$CI_PROJECT_NAME.dd_api_key --with-decryption --query "Parameter.Value" --out text)
fi

cat <<EOT > /etc/datadog-agent/datadog.yaml
api_key: $DD_API_KEY
hostname: $DD_HOSTNAME
site: datadoghq.com

tags:
  - bp_app:$BP_PYTHON_SCENARIO_DIR
  - bp_branch:$CI_COMMIT_REF_NAME
  - bp_commit_sha:$CI_COMMIT_SHORT_SHA
  - bp_project:dd-trace-py
  - ci_job_id:$CI_JOB_ID
  - ci_job_name:$CI_JOB_NAME
  - ci_pipeline_id:$CI_PIPELINE_ID
  - dd_trace_version:$DDTRACE_INSTALL_VERSION
  - dd_rc_enabled:$DD_REMOTE_CONFIGURATION_ENABLED
  - dd_telemetry_enabled:$DD_INSTRUMENTATION_TELEMETRY_ENABLED
  - appsec:$DD_APPSEC_ENABLED
  - crashtracing:$DD_CRASHTRACKING_ENABLED
  - runtime_metrics:$DD_RUNTIME_METRICS_ENABLED
  - scenario:$SCENARIO

process_config:
  process_collection:
    enabled: true

  process_discovery:
    enabled: true
    interval: 10s
EOT

cat <<EOT > /etc/datadog-agent/conf.d/process.d/conf.yaml
init_config:
  pid_cache_duration: 15
  access_denied_cache_duration: 15
  shared_process_list_cache_duration: 15

instances:
  - name: gunicorn-or-uwsgi
    search_string:
      - gunicorn
      - uwsgi

  - name: k6
    search_string:
      - k6
EOT

if [[ "$DD_REMOTE_CONFIGURATION_ENABLED" == true || "$DD_REMOTE_CONFIGURATION_ENABLED" == "true" || "$DD_REMOTE_CONFIGURATION_ENABLED" == 1 ]]; then
    cat <<EOT >> /etc/datadog-agent/datadog.yaml
remote_configuration:
  enabled: true
EOT
fi
