# Stack v2 / Echion Fuzz Targets

Fuzz targets for the stack v2 profiler's echion-based unwinder.
All targets are built with **libFuzzer + ASAN + UBSAN** inside a Docker image.

## Targets

| Binary | What it exercises |
|--------|-------------------|
| `fuzz_echion_remote_read` | Code object parsing, line table decoding (`Frame::create`), and `StackChunk` update/resolve (3.11+) |
| `fuzz_echion_strings` | `StringTable::key()` (PyUnicode) and `pybytes_to_bytes_and_size()` (PyBytes) |
| `fuzz_echion_mirrors` | `MirrorSet::create()` — PySetObject header reading, size bounds, table iteration |
| `fuzz_echion_stacks` | Full frame/stack unwinding: `unwind_frame` (chain walk) and `unwind_python_stack` (from PyThreadState) |

## Build the Docker image

```bash
docker build -f docker/Dockerfile.fuzz -t ddtrace-py-stackv2-fuzz .
```

## Run a single target

The default CMD runs `fuzz_echion_remote_read`:

```bash
docker run --rm -it ddtrace-py-stackv2-fuzz
```

To run a specific target, override the command:

```bash
docker run --rm -it ddtrace-py-stackv2-fuzz /tmp/fuzz/build/fuzz/fuzz_echion_strings /tmp/fuzz/ -artifact_prefix=/tmp/fuzz/
docker run --rm -it ddtrace-py-stackv2-fuzz /tmp/fuzz/build/fuzz/fuzz_echion_mirrors /tmp/fuzz/ -artifact_prefix=/tmp/fuzz/
docker run --rm -it ddtrace-py-stackv2-fuzz /tmp/fuzz/build/fuzz/fuzz_echion_stacks /tmp/fuzz/ -artifact_prefix=/tmp/fuzz/
```

## Persist corpus and artifacts with a volume mount

Mount a host directory to `/tmp/fuzz/` so corpus inputs and crash artifacts survive container restarts:

```bash
mkdir -p .fuzz
docker run --rm -it -v "$PWD/.fuzz:/tmp/fuzz/" ddtrace-py-stackv2-fuzz \
    /tmp/fuzz/build/fuzz/fuzz_echion_remote_read /tmp/fuzz/ -artifact_prefix=/tmp/fuzz/
```

## Quick sanity check (all targets)

Run each target for 10 seconds to verify they start without errors:

```bash
for target in fuzz_echion_remote_read fuzz_echion_strings fuzz_echion_mirrors fuzz_echion_stacks; do
    echo "=== $target ==="
    docker run --rm ddtrace-py-stackv2-fuzz \
        /tmp/fuzz/build/fuzz/$target /tmp/fuzz/ -artifact_prefix=/tmp/fuzz/ -max_total_time=10
done
```

## List built binaries (manifest)

The build script writes a manifest of all built fuzz binaries:

```bash
docker run --rm ddtrace-py-stackv2-fuzz cat /tmp/fuzz/build/fuzz_binaries.txt
```
