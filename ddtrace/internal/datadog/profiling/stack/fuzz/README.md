# Stack Profiler / Echion Fuzz Targets

Fuzz targets for the Python stack profiler unwinder (echion).
All targets are built with **libFuzzer + ASan + UBSan** inside a Docker image
and orchestrated by [fuzzydog](https://datadoghq.atlassian.net/wiki/spaces/fuzzing).

## Targets

| Binary | What it exercises |
|--------|-------------------|
| `fuzz_echion_frame_create` | Code object parsing, line table decoding (`Frame::create`), and `StackChunk` update/resolve (3.11+) |
| `fuzz_echion_frame_read` | `Frame::read` (`_PyInterpreterFrame` / `PyFrameObject` parsing, owner bits, lasti) and `Frame::get` (LRU cache lookup/store) |
| `fuzz_echion_greenlet` | `GreenletInfo::unwind` — version-specific greenlet frame resolution and frame chain walk |
| `fuzz_echion_interp` | `for_each_interp` — interpreter linked-list traversal, cycle detection, iteration bounds |
| `fuzz_echion_long` | `pylong_to_llong` — PyLong object parsing (compact/multi-digit) from remote memory (3.12+) |
| `fuzz_echion_mirrors` | `MirrorSet::create()` — PySetObject header reading, size bounds, table iteration |
| `fuzz_echion_pyunicode` | `pyunicode_to_utf8` — PyUnicodeObject header validation and character buffer copy |
| `fuzz_echion_stacks` | Full frame/stack unwinding: `unwind_frame` (chain walk) and `unwind_python_stack` (from PyThreadState) |
| `fuzz_echion_strings` | `StringTable::key()` (PyUnicode) and `pybytes_to_bytes_and_size()` (PyBytes) |
| `fuzz_echion_task_unwind` | `TaskInfo::unwind` — coroutine/await chain walk, including uvloop wrapper frame detection |
| `fuzz_echion_tasks` | `GenInfo::create` (coroutine await chains) and `TaskInfo::create` (task headers, name resolution, waiter chains) |
| `fuzz_echion_thread_unwind` | `ThreadInfo::unwind` — thread stack path only (no asyncio or greenlets) |
| `fuzz_echion_thread_unwind_tasks` | `ThreadInfo::unwind` with an asyncio loop attached, exercising task unwinding |

## Build the Docker image

```bash
docker build -f docker/Dockerfile.fuzz \
    --build-arg FUZZ_BASE_IMAGE=registry.ddbuild.io/dd-trace-py:vXXX-fuzz_base \
    --build-arg PYTHON_VERSION=3.12 \
    -t ddtrace-py-stackv2-fuzz .
```

## Run with fuzzydog

The default CMD runs `fuzz_echion_frame_create` via fuzzydog.
`FUZZYDOG_AUTH_TOKEN` must be set in the environment.

```bash
export FUZZYDOG_AUTH_TOKEN=$(ddtool auth token security-fuzzing-platform --datacenter=us1.ddbuild.io)

docker run --rm -it -e FUZZYDOG_AUTH_TOKEN ddtrace-py-stackv2-fuzz
```

To run a specific target, override the command:

```bash
export FUZZYDOG_AUTH_TOKEN=$(ddtool auth token security-fuzzing-platform --datacenter=us1.ddbuild.io)

docker run --rm -it -e FUZZYDOG_AUTH_TOKEN ddtrace-py-stackv2-fuzz \
    fuzzydog fuzzer run dd-trace-py-local fuzz_echion_strings \
    --type libfuzzer --team profiling-python \
    --build-path /fuzzer/builds/ \
    --skip-dl-build --skip-dl-inputs \
    --slack-channel profiling-python-ops \
    --repository-url https://github.com/DataDog/dd-trace-py
```

## List built binaries (manifest)

The build script writes a manifest of all built fuzz binaries.
Binaries are copied to `/fuzzer/builds/` in the final image:

```bash
docker run --rm ddtrace-py-stackv2-fuzz ls -la /fuzzer/builds/
```
