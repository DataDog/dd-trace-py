#!/usr/bin/env bash -eu

set -eu

PREFIX=${1}

AUSTIN_INTERVAL=200  # usec
AUSTIN_EXPOSURE=5  # sec

test -f ${PREFIX}/gunicorn.pid && (kill -9 `cat ${PREFIX}/gunicorn.pid` ; sleep 3) || rm -f ${PREFIX}/gunicorn.pid
pkill k6 || true
test -d ${PREFIX}/artifacts && rm -rf ${PREFIX}/artifacts || mkdir -p ${PREFIX}/artifacts

sudo echo "sudo OK"

function profile_with_load {
    name=${1}

    sleep 3
    ${PREFIX}/k6*/k6 run --quiet scripts/profiles/django-simple/k6-load.js &
        sleep 2
        sudo ${PREFIX}/austin -bsCi ${AUSTIN_INTERVAL} -o ${PREFIX}/artifacts/${name}.mojo -p `cat ${PREFIX}/gunicorn.pid` -x ${AUSTIN_EXPOSURE}
    pkill k6
}

source ${PREFIX}/bin/activate

export DJANGO_SETTINGS_MODULE="config.settings.production"
export DJANGO_ALLOWED_HOSTS="127.0.0.1"
export DJANGO_SECRET_KEY="SECRET_KEY"
export DATABASE_URL="sqlite:///django.db"

# Tag traces with HTTP headers to benchmark the related code
export DD_TRACE_HEADER_TAGS="User-Agent:http.user_agent,Referer:http.referer,Content-Type:http.content_type,Etag:http.etag"

# Baseline
pushd ${PREFIX}/trace-examples/python/django/django-simple
    gunicorn config.wsgi --pid ${PREFIX}/gunicorn.pid > /dev/null &
    echo "Done"
popd
profile_with_load "baseline"
kill $(cat ${PREFIX}/gunicorn.pid)

pushd ${PREFIX}/trace-examples/python/django/django-simple
    ddtrace-run gunicorn config.wsgi --pid ${PREFIX}/gunicorn.pid > /dev/null &
popd
profile_with_load "head"
kill $(cat ${PREFIX}/gunicorn.pid)

sudo chown -R $(id -u):$(id -g) ${PREFIX}/artifacts/*

echo -n "Converting MOJO to Austin ... "
mojo2austin ${PREFIX}/artifacts/head.mojo     ${PREFIX}/artifacts/head.austin.tmp
mojo2austin ${PREFIX}/artifacts/baseline.mojo ${PREFIX}/artifacts/baseline.austin.tmp
echo "[done]"

echo -n "Diffing ... "
python scripts/diff.py \
    ${PREFIX}/artifacts/head.austin.tmp \
    ${PREFIX}/artifacts/baseline.austin.tmp \
    ${PREFIX}/artifacts/baseline_head.diff
echo "[done]"

rm ${PREFIX}/artifacts/*.austin.tmp

head -n 25 ${PREFIX}/artifacts/baseline_head.diff.top
