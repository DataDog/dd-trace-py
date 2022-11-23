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
    ${PREFIX}/k6*/k6 run --quiet scripts/profiles/flask-simple/k6-load.js &
        sleep 2
        sudo ${PREFIX}/austin -bsCi ${AUSTIN_INTERVAL} -o ${PREFIX}/artifacts/${name}.mojo -p `cat ${PREFIX}/gunicorn.pid` -x ${AUSTIN_EXPOSURE}
    pkill k6
}

source ${PREFIX}/bin/activate

# Tag traces with HTTP headers to benchmark the related code
export DD_TRACE_HEADER_TAGS="User-Agent:http.user_agent,Referer:http.referer,Content-Type:http.content_type,Etag:http.etag"

# Baseline
pushd ${PREFIX}/app
    gunicorn -b 0.0.0.0 app:app --pid ${PREFIX}/gunicorn.pid > /dev/null &
    echo "Done"
popd
profile_with_load "baseline"
# Baseline and with 1.x and with current
kill $(cat ${PREFIX}/gunicorn.pid)

pushd ${PREFIX}/app
    ddtrace-run gunicorn -b 0.0.0.0 app:app --pid ${PREFIX}/gunicorn.pid > /dev/null &
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
