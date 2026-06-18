# dd_offcpu (spike)

Standalone C + libbpf sidecar that measures **exact kernel off-CPU time** for a target
Python process and (eventually) emits a pprof profile with Python frames. It replaces the
userspace `wall_time − cpu_time` approximation with kernel-measured intervals.

> **Status:** spike scaffold. The BPF program and daemon plumbing are in place; native
> symbolization, Python frame walking, and libdatadog pprof emit are stubbed. See the
> design doc (`ebpf-offcpu-profiler-design.md`) for scope and milestones.

This component is **Linux-only** (eBPF). It will not build on macOS — the CMake configure
step fails fast there.

## Requirements

- Linux kernel **≥ 5.8** with BTF (`CONFIG_DEBUG_INFO_BTF=y`, i.e. `/sys/kernel/btf/vmlinux`
  exists). The 5.8 floor comes from `BPF_MAP_TYPE_RINGBUF`. Note distros backport: RHEL 8
  (4.18) works; Ubuntu 20.04 GA (5.4) does not — use its HWE kernel.
- Toolchain: `clang`, `llvm`, `bpftool`, `libelf` (dev), `zlib` (dev), `cmake` ≥ 3.19,
  plus `make`/`gcc` and `git` to build the vendored libbpf.

> **libbpf is vendored, not taken from the distro.** The build fetches a pinned libbpf
> release (default `v1.5.1`, see `LIBBPF_VERSION` in `CMakeLists.txt`) and links it
> statically. This is deliberate: Ubuntu 22.04 ships libbpf **0.5.0**, which predates
> `BTF_KIND_ENUM64` (added in libbpf 1.0) and cannot parse the BTF emitted by kernels
> ≥ 6.0 — it fails at load with `failed to find valid kernel BTF`. Because libbpf is
> fetched at build time, the first build needs network access. `bpftool` is still taken
> from the system and must be reasonably recent (≥ 5.x).

### Debian / Ubuntu

```bash
sudo apt-get install -y \
    clang llvm libelf-dev zlib1g-dev cmake build-essential git \
    linux-tools-common linux-tools-"$(uname -r)"   # provides bpftool
```

### Fedora / RHEL

```bash
sudo dnf install -y clang llvm bpftool elfutils-libelf-devel zlib-devel cmake make gcc git
```

## Build

From the repo root:

```bash
cmake -S ddtrace/internal/datadog/profiling/dd_offcpu -B build/dd_offcpu
cmake --build build/dd_offcpu
```

The first build fetches and statically builds the pinned libbpf, generates `vmlinux.h` from
the running kernel's BTF, compiles the BPF program to a CO-RE object, generates the libbpf
skeleton, and links the `dd_offcpu` binary into `build/dd_offcpu/`. The resulting binary has
no dynamic dependency on libbpf (`ldd` shows only libelf/libz/libc).

## Grant capabilities (once)

The daemon needs `CAP_BPF` + `CAP_PERFMON` to load the program and read stacks. Set them on
the binary so it can run as a plain (non-root) subprocess afterwards — same model as
`dumpcap`/`ping`:

```bash
sudo setcap cap_bpf,cap_perfmon=ep build/dd_offcpu/dd_offcpu
```

## Run

```bash
# Profile a running Python process by PID until ctrl-c.
build/dd_offcpu/dd_offcpu --pid <pid>

# Options:
#   --pid <pid>           target process (required)
#   --min-block-us <us>   drop off-CPU intervals shorter than this (kernel-side)
#   --output <file>       pprof output path (default: offcpu.pb.gz)
```

A quick end-to-end target is `scripts/demo_offcpu_approximation.py`, which spawns threads
that block on sleep / locks / I/O — useful for eyeballing the off-CPU output and comparing
against the existing approximation (spike milestone 6).

### PID namespaces / containers

`--pid` is the pid **as seen from where you launch `dd_offcpu`** — i.e. the value the target
returns from `getpid()` in its own PID namespace. In the intended deployment ddtrace spawns
the sidecar from inside the same container as the app, so this is simply the app's pid; no
host-pid translation is needed. The daemon reads the target's PID-namespace inode from
`stat("/proc/<pid>/ns/pid")` and the BPF program matches each task by (pid, ns inode) in that
namespace rather than by the global init-namespace pid. Reported thread ids are likewise
namespace-local, so they line up with the `/proc` the daemon sees for frame walking. This
works whether or not the target is containerized.

## Layout

```
dd_offcpu/
├── CMakeLists.txt        # vmlinux.h -> BPF object -> skeleton -> dd_offcpu
├── bpf/offcpu.bpf.c      # CO-RE tp_btf/sched_switch off-CPU accumulation + ringbuf
├── include/offcpu.h      # shared BPF<->userspace event struct
└── src/
    ├── main.c            # arg parse, skeleton load/attach, ring buffer poll loop
    ├── symbolize.{c,h}   # native ELF symbolizer (stub)
    └── pysym.{c,h}       # Python frame walker (stub)
```
