#!/usr/bin/env bash
set -ex

export PYTHON_VERSION=3.9.6
export PYENV_ROOT=/pyenv
export PATH="$PYENV_ROOT/shims:$PYENV_ROOT/bin:$PATH"
export VENV_DDTRACE=/app/.venv_ddtrace/
export PG_RETRIES=5

eval "$(pyenv init -)"

pyenv global "$PYTHON_VERSION"

source ${VENV_DDTRACE}/bin/activate

export CONDUIT_SECRET="something-really-secret"
export FLASK_APP=./autoapp.py
export DATABASE_URL="postgresql://postgres:password@localhost"

export DD_INSTRUMENTATION_TELEMETRY_ENABLED=true
export DD_TRACE_HEALTH_METRICS_ENABLED=true
export DD_TRACE_ENABLED=true
export DD_PROFILING_ENABLED=true
export DD_PROFILING_MAX_FRAMES=512
export DD_RUNTIME_METRICS_ENABLED=true
export DD_APPSEC_ENABLED=true
export PYTHONUNBUFFERED=1
export DD_ENV=prod


service postgresql start

service datadog-agent start
echo "RUN WSGI APP"
echo "=============================="
echo "DD_API_KEY=${DD_API_KEY}"
echo "DD_SITE=${DD_SITE}"
echo "DD_SERVICE=${DD_SERVICE}"
echo "=============================="
ddtrace-run uwsgi --http 0.0.0.0:3000 --enable-threads --module autoapp:app