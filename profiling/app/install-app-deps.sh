#!/usr/bin/env bash
set -ex

export PYTHON_VERSION=3.9.6
export PYENV_ROOT=/pyenv
export PATH="$PYENV_ROOT/shims:$PYENV_ROOT/bin:$PATH"
export VENV_DDTRACE=/app/.venv_ddtrace/
export PG_RETRIES=5

export CONDUIT_SECRET="something-really-secret"
export FLASK_APP=./autoapp.py
export DATABASE_URL="postgresql://postgres:password@localhost"

service postgresql restart

apt-get install -y libpq-dev

eval "$(pyenv init -)"
pyenv global "$PYTHON_VERSION"

python3 -m venv ${VENV_DDTRACE}
source ${VENV_DDTRACE}/bin/activate

pip install awscli virtualenv uwsgi py-spy
pip install -r requirements.txt

if [ -z "$DDTRACE_INSTALL_VERSION" ]
then
  echo "Install dd-trace-py"
else
  echo "Install DEFAULT dd-trace-py version"
  export DDTRACE_INSTALL_VERSION="git+https://github.com/Datadog/dd-trace-py@1.x"
fi

echo "=============================="
echo "DDTRACE_INSTALL_VERSION=${DDTRACE_INSTALL_VERSION}"
echo "=============================="

pip install $DDTRACE_INSTALL_VERSION

# wait for postgres to start up
until pg_isready -h localhost > /dev/null 2>&1 || [ $PG_RETRIES -eq 0 ]; do
  echo "Waiting for postgres server, $((PG_RETRIES--)) remaining attempts..."
  sleep 1
done

flask db init
flask db migrate
flask db upgrade

deactivate