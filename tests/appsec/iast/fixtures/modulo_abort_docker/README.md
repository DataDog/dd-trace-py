# IAST Modulo Abort Reproducer

This is a minimal app-style reproducer for the IAST `%` formatting abort. Flask
is only used to provide a normal request-tainted string; the failure is in IAST
modulo-format propagation, so the same class of input can affect any
IAST-enabled app that formats tainted data with `%`.

Build and run:

```sh
docker build \
  -f tests/appsec/iast/fixtures/modulo_abort_docker/Dockerfile \
  -t ddtrace-iast-modulo-abort-repro \
  tests/appsec/iast/fixtures/modulo_abort_docker

docker run --rm -p 8000:8000 ddtrace-iast-modulo-abort-repro
```

Healthy request:

```sh
curl 'http://localhost:8000/format?value=hello'
```

Abort trigger on vulnerable builds:

```sh
curl --globoff 'http://localhost:8000/format?value=a:%2B-b'
```

`%2B` is the URL-encoded `+`, so the app receives the tainted value `a:+-b`.
On vulnerable builds the container exits after the native modulo aspect aborts.

To test a specific released package, pass a pip requirement:

```sh
docker build \
  --build-arg 'DDTRACE_SPEC=ddtrace[appsec]==<version>' \
  -f tests/appsec/iast/fixtures/modulo_abort_docker/Dockerfile \
  -t ddtrace-iast-modulo-abort-repro \
  tests/appsec/iast/fixtures/modulo_abort_docker
```

Do not run this reproducer with `DD_TRACE_ENABLED=false`: the Flask request
instrumentation is what marks the query parameter as IAST-tainted.
