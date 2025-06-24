#!/usr/bin/env bash

set -e

cd $REPOSITORY_ROOT

echo "Starting $BP_PYTHON_SCENARIO_DIR server..."

# Install wheel to avoid compilation of dependencies
pip install wheel

if [ "$BP_PYTHON_SCENARIO_DIR" == "flask-realworld" ]; then
  export CONDUIT_SECRET="something-really-secret"
  export FLASK_APP=./autoapp.py
  export DATABASE_URL="postgresql://postgres:password@localhost"

  if [ ! -d "./src/flask-realworld" ]; then
    git clone https://github.com/Datadog/flask-realworld-example-app ./src/flask-realworld
    pip install uwsgi
  fi
fi

if [ ! -d "./src/$BP_PYTHON_SCENARIO_DIR" ]; then
  echo "Unknown macrobenchmark scenario dir BP_PYTHON_SCENARIO_DIR=$BP_PYTHON_SCENARIO_DIR"
  exit 1
fi
cd ./src/$BP_PYTHON_SCENARIO_DIR

pip install -r requirements.txt

if [ "$DD_BENCHMARKS_CONFIGURATION" == "baseline" ]; then
  if [ "$BP_PYTHON_SCENARIO_DIR" == "flask-realworld" ]; then
    uwsgi --disable-logging \
      --http 0.0.0.0:8000 \
      --workers=9 \
      --threads=2 \
      --log-master \
      --enable-threads \
      --module autoapp:app
  else
    gunicorn -c gunicorn.conf.py
  fi
else
  pip install ${DDTRACE_INSTALL_VERSION}

  export PERF_TRACER_ENABLED=1
  export PERF_PROFILER_ENABLED=0

  echo "DD_REMOTE_CONFIGURATION_ENABLED=$DD_REMOTE_CONFIGURATION_ENABLED, DD_INSTRUMENTATION_TELEMETRY_ENABLED=$DD_INSTRUMENTATION_TELEMETRY_ENABLED"

  if [ "$BP_PYTHON_SCENARIO_DIR" == "flask-realworld" ]; then
    uwsgi --lazy-apps --import=ddtrace.bootstrap.sitecustomize \
      --disable-logging \
      --http 0.0.0.0:8000 \
      --workers=9 \
      --threads=2 \
      --log-master \
      --enable-threads \
      --module autoapp:app
  else
    ddtrace-run gunicorn -c gunicorn.conf.py
  fi
fi
