# Before-Fork PeriodicThread Reproducer

This is a minimal standalone reproducer for the before-fork hook waiting
unboundedly on a blocked `PeriodicThread` callback. It does not actually fork;
it calls the same registered ddtrace before-fork hook directly and bounds the
wait so the container proves the issue without hanging forever.

Build and run:

```sh
docker build \
  -f tests/internal/fixtures/before_fork_periodic_docker/Dockerfile \
  -t ddtrace-before-fork-periodic-repro \
  tests/internal/fixtures/before_fork_periodic_docker

docker run --rm ddtrace-before-fork-periodic-repro
```

Expected result on vulnerable builds:

```text
calling ddtrace.internal.threads._before_fork()
REPRODUCED: _before_fork is still blocked after 1.0 seconds
```

The container exits `1` when the bug is reproduced. Fixed builds should print
`safe: _before_fork returned without waiting for the blocked callback` and exit
`0`.

To test a specific released package, pass a pip requirement:

```sh
docker build \
  --build-arg 'DDTRACE_SPEC=ddtrace==<version>' \
  -f tests/internal/fixtures/before_fork_periodic_docker/Dockerfile \
  -t ddtrace-before-fork-periodic-repro \
  tests/internal/fixtures/before_fork_periodic_docker
```
