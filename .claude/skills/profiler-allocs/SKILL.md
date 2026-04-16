---
name: profiler-allocs
description: >
  Analyze native (C/C++/Rust) memory allocations made by the dd-trace-py
  profiler. Builds the profiler in Docker, runs it under ddprof to capture
  allocation profiles, and analyzes allocation count and live heap using
  go tool pprof. Use this to measure the profiler's fragmentation impact
  and identify allocation hotspots in profiler code.
allowed-tools:
  - Bash
  - Read
  - Grep
  - Glob
  - Edit
  - Write
---

# Profiler Native Allocation Analysis

This skill captures and analyzes native memory allocations from the dd-trace-py
profiler's own code (not the application being profiled). It uses
[ddprof](https://github.com/DataDog/ddprof) to profile the profiler, then
`go tool pprof` to analyze the results.

**Primary metric**: allocation count (fragmentation impact), not bytes.

## Prerequisites

- Docker (with `--privileged` capability for `perf_event_open`)
- `go` (for `go tool pprof`)
- `zstd` (for decompressing ddprof output)

## Workflow

### Step 1: Build the Docker Image

Check if the image already exists. Only rebuild if profiler source code changed.

```bash
# Check if image exists
docker images ddtrace-profiler-allocs --format "{{.ID}}" | head -1

# Build (from repo root)
docker build -t ddtrace-profiler-allocs -f scripts/profiler-allocs/Dockerfile .
```

The build takes ~5 minutes (Rust + CMake compilation). It:
- Installs pyenv + Python 3.12 with `--enable-shared`
- Installs Rust toolchain
- Builds dd-trace-py with `DD_FAST_BUILD=1`
- Downloads ddprof v0.25.0

### Step 2: Capture Profiles

```bash
mkdir -p artifacts
docker run --rm --privileged \
  -e TEST_DURATION=30 \
  -v $(pwd)/artifacts:/artifacts \
  ddtrace-profiler-allocs
```

- `--privileged` is required for ddprof's `perf_event_open`
- `TEST_DURATION` controls workload duration (default 30s)
- ddprof writes `.pprof.zst` files to `/artifacts/`
- Upload period is 10s, so a 30s run produces ~3 steady-state profiles

### Step 3: Analyze

```bash
scripts/profiler-allocs/analyze.sh [artifacts_dir]
```

The analysis script:
1. Decompresses `.pprof.zst` files
2. Reports profiler allocation count over time (excluding app workload)
3. Shows cumulative call chains driving allocations
4. Reports live heap and live object counts
5. Prints `go tool pprof` commands for interactive flamegraph exploration

### Interpreting Results

**Allocation count** (`alloc-samples`):
- Focus filter: profiler code paths (dd_wrapper, ddup, libdatadog, etc.)
- Ignore filter: app workload (bytearray, PyList_Append going through _memalloc hooks)
- Steady-state rate should be ~2-6 allocs/second
- Higher rates suggest missing pre-allocation or unnecessary object churn

**Live heap** (`inuse-space`):
- Shows what's retained — identifies memory that could be freed or pooled
- `libdd_alloc::VirtualAllocator` is the arena allocator (expected to retain)

**Key code paths to watch**:
- `UploaderBuilder::build()` — creates exporter + serializes each cycle
- `ddog_prof_Exporter_new()` — HTTP client + TLS setup (should be reused)
- `Sampler::sampling_thread()` — stack sampling (should not allocate)
- `StackRenderer::render_frame()` — frame rendering (should reuse buffers)
- `intern_string()` — string dedup (grows with unique strings)

### Interactive Exploration

```bash
# Allocation count flamegraph (profiler only)
go tool pprof -http=:8080 -sample_index=alloc-samples \
  -focus='dd_wrapper|ddup|libdatadog|libdd|Datadog::' \
  -ignore='bytearray|PyByteArray' \
  artifacts/ddprof*.pprof

# Live heap flamegraph
go tool pprof -http=:8080 -sample_index=inuse-space \
  -focus='dd_wrapper|ddup|libdatadog|libdd|Datadog::' \
  artifacts/ddprof*.pprof
```

## Files

| File | Purpose |
|------|---------|
| `scripts/profiler-allocs/Dockerfile` | Docker image: dd-trace-py build + ddprof |
| `scripts/profiler-allocs/test_app.py` | Multi-threaded workload exercising the profiler |
| `scripts/profiler-allocs/build.sh` | Image build helper |
| `scripts/profiler-allocs/analyze.sh` | pprof analysis (allocation count + live heap) |
| `scripts/profiler-allocs/ANALYSIS.md` | Baseline analysis and improvement opportunities |

## Reference: ddprof Flags

```
ddprof -l notice --preset cpu_live_heap \
  --debug_pprof_prefix /artifacts/ddprof \
  -u 10 \
  -- python3 /app/test_app.py
```

- `-l notice`: log level
- `--preset cpu_live_heap`: CPU + allocations + live heap
- `--debug_pprof_prefix`: saves pprof files locally (zstd compressed)
- `-u 10`: upload/export period in seconds
