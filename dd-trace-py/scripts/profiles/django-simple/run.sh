#!/usr/bin/env bash -eu

set -eu

PREFIX=${1}

AUSTIN_INTERVAL=200  # usec
AUSTIN_EXPOSURE=5  # sec

test -f ${PREFIX}/gunicorn.pid && (kill -9 `cat ${PREFIX}/gunicorn.pid` ; sleep 3) || rm -f ${PREFIX}/gunicorn.pid
pkill k6 || true
pkill -9 -f uwsgi || true
test -d ${PREFIX}/artifacts && rm -rf ${PREFIX}/artifacts
mkdir -p ${PREFIX}/artifacts

sudo echo "sudo OK"

sudo rm -f ${PREFIX}/uwsgi.pid

function profile_with_load {
    name=${1}
    scenario=${2}

    echo "- profiling for ${name}"

    sleep 3
    echo "Starting load"
    ${PREFIX}/k6*/k6 run --quiet scripts/profiles/django-simple/k6-${scenario}.js &
        sleep 2
        echo "Attaching Austin to $(cat ${PREFIX}/uwsgi.pid)"
        sudo `which austin` -bsCi ${AUSTIN_INTERVAL} -o ${PREFIX}/artifacts/${scenario}_${name}.mojo -p `cat ${PREFIX}/uwsgi.pid` -x ${AUSTIN_EXPOSURE}
        LC_ALL=C sed -i 's|/home/runner/work/dd-trace-py/dd-trace-py/ddtrace/||g' ${PREFIX}/artifacts/${scenario}_${name}.mojo
    echo "Stopping load"
    pkill k6
}

source ${PREFIX}/bin/activate

export DATABASE_URL="sqlite:///django.db"
export DJANGO_ALLOWED_HOSTS="127.0.0.1"
export DEVELOPMENT_MODE=True

# Tag traces with HTTP headers to benchmark the related code
export DD_TRACE_HEADER_TAGS="User-Agent:http.user_agent,Referer:http.referer,Content-Type:http.content_type,Etag:http.etag"


function run_scenario {
    scenario=${1}

    echo "Running scenario ${scenario}"

    # Baseline
    pushd ${PREFIX}/trace-examples/python/django/sample-django
        uwsgi --http :8080 --enable-threads --module mysite.wsgi --pidfile ${PREFIX}/uwsgi.pid 2> /dev/null &
        echo "Done"
    popd
    profile_with_load "baseline" ${scenario}
    echo "Stopping uwsgi"
    uwsgi --stop ${PREFIX}/uwsgi.pid
    pkill -9 -f uwsgi || true

    pushd ${PREFIX}/trace-examples/python/django/sample-django
        uwsgi --http :8080 --enable-threads --module mysite.wsgi --pidfile ${PREFIX}/uwsgi.pid --import=ddtrace.bootstrap.sitecustomize 2> /dev/null &
    popd
    profile_with_load "head" ${scenario}
    echo "Stopping uwsgi"
    uwsgi --stop ${PREFIX}/uwsgi.pid
    pkill -9 -f uwsgi || true

    sudo chown -R $(id -u):$(id -g) ${PREFIX}/artifacts/*

    echo -n "Converting MOJO to Austin ... "
    mojo2austin ${PREFIX}/artifacts/${scenario}_head.mojo     ${PREFIX}/artifacts/${scenario}_head.austin.tmp
    mojo2austin ${PREFIX}/artifacts/${scenario}_baseline.mojo ${PREFIX}/artifacts/${scenario}_baseline.austin.tmp
    echo "[done]"

    echo -n "Diffing ... "
    python scripts/diff.py \
        ${PREFIX}/artifacts/${scenario}_head.austin.tmp \
        ${PREFIX}/artifacts/${scenario}_baseline.austin.tmp \
        ${PREFIX}/artifacts/${scenario}_baseline_head.diff
    echo "[done]"

    rm ${PREFIX}/artifacts/*.austin.tmp

    head -n 25 ${PREFIX}/artifacts/${scenario}_baseline_head.diff.top
}


run_scenario "load"
run_scenario "exc"
