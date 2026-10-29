#! /bin/bash
set -e

FIXTURES=tests/appsec/integrations/pygoat_tests/fixtures
rm -rf pygoat
tar -xf "$FIXTURES/pygoat.xz" -C .
python -m pip --version
if python -c 'import sys; sys.exit(sys.version_info < (3, 15))'; then
    python -m pip install -r "$FIXTURES/requirements-python315.txt"
    export PYTHONPATH="$(pwd)/$FIXTURES${PYTHONPATH:+:$PYTHONPATH}"
    export DJANGO_SETTINGS_MODULE=pygoat_python315_settings
    # Keep the Python YAML implementation so IAST can instrument it.
    python -m pip install --no-cache-dir --force-reinstall --no-binary=PyYAML \
        --config-settings=--global-option=--without-libyaml PyYAML==6.0.3
else
    python -m pip install -r pygoat/requirements.txt
    python -m pip install --no-cache-dir --force-reinstall pyyaml==6.0.1 --global-option='--without-libyaml'
fi
python pygoat/manage.py migrate
python pygoat/manage.py loaddata "$FIXTURES/"*.json
python -m ddtrace.commands.ddtrace_run python pygoat/manage.py runserver --noreload 0.0.0.0:8321 > pygoat/server.log 2>&1 &
pygoat_pid=$!
trap 'kill "$pygoat_pid" 2>/dev/null || true' EXIT

# Surface startup failures instead of reporting a connection error in every test.
ready=false
for attempt in {1..30}; do
    if curl --silent --fail --output /dev/null http://127.0.0.1:8321/login/; then
        ready=true
        break
    fi
    if ! kill -0 "$pygoat_pid" 2>/dev/null; then
        break
    fi
    sleep 1
done
if [ "$ready" != true ]; then
    cat pygoat/server.log
    exit 1
fi
python -m pytest -vvv "$@"
