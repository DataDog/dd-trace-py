#! /bin/bash
set -e

rm -rf pygoat || true
tar -xf tests/appsec/integrations/pygoat_tests/fixtures/pygoat.xz -C .
pip install -r pygoat/requirements.txt
pip install --no-cache-dir --force-reinstall pyyaml==6.0.1 --global-option='--without-libyaml'
python3 pygoat/manage.py migrate
python3 pygoat/manage.py loaddata tests/appsec/integrations/pygoat_tests/fixtures/*.json
python -m ddtrace.commands.ddtrace_run python3 pygoat/manage.py runserver 0.0.0.0:8321 > /dev/null 2>&1 & echo $! > pygoat.pid
sleep 5
python -m pytest -vvv $1
kill $(cat pygoat.pid) || true
rm -f pygoat.pid pygoat || true