# Stack v2 / Echion Fuzz Targets

Fuzz targets for the stack v2 profiler's echion-based unwinder.
All targets are built with **libFuzzer + ASAN + UBSAN** inside a Docker image
and orchestrated by [fuzzydog](https://datadoghq.atlassian.net/wiki/spaces/fuzzing).

## Targets

| Binary | What it exercises |
|--------|-------------------|
| `fuzz_echion_remote_read` | Code object parsing, line table decoding (`Frame::create`), and `StackChunk` update/resolve (3.11+) |
| `fuzz_echion_strings` | `StringTable::key()` (PyUnicode) and `pybytes_to_bytes_and_size()` (PyBytes) |
| `fuzz_echion_mirrors` | `MirrorSet::create()` — PySetObject header reading, size bounds, table iteration |
| `fuzz_echion_stacks` | Full frame/stack unwinding: `unwind_frame` (chain walk) and `unwind_python_stack` (from PyThreadState) |

## Build the Docker image

```bash
docker build -f docker/Dockerfile.fuzz --build-arg PYTHON_IMAGE_TAG=3.12.0 -t ddtrace-py-stackv2-fuzz .
```

## Run with fuzzydog

The default CMD runs `fuzz_echion_remote_read` via fuzzydog.
`FUZZYDOG_AUTH_TOKEN` must be set in the environment:

```bash
docker run --rm -it -e FUZZYDOG_AUTH_TOKEN ddtrace-py-stackv2-fuzz
```

To run a specific target, override the command:

```bash
docker run --rm -it -e FUZZYDOG_AUTH_TOKEN ddtrace-py-stackv2-fuzz \
    fuzzydog fuzzer run dd-trace-py-local fuzz_echion_strings \
    --type libfuzzer --team profiling-python \
    --build-path /fuzzer/builds/ \
    --skip-dl-build --skip-dl-inputs \
    --slack-channel fuzzing-ops \
    --repository-url https://github.com/DataDog/dd-trace-py
```

## List built binaries (manifest)

The build script writes a manifest of all built fuzz binaries.
Binaries are copied to `/fuzzer/builds/` in the final image:

```bash
docker run --rm ddtrace-py-stackv2-fuzz ls -la /fuzzer/builds/
```
