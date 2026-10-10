#!/usr/bin/env python3
"""CPU probe: per-CPU microbenchmarks that flag asymmetric cores.

EXPERIMENT (do not merge): CPU-asymmetry probe on benchmarking hosts
(PR #20052 / APMSP-4059). Known result to reproduce: on the benchmarking
hosts, CPU 24 runs allocation-heavy code ~1.4x slower than its neighbors
(clean on 25/36/37) while every OS-level counter looks identical, so the
probe measures the work itself on every CPU instead of watching counters.

Seven scenarios run on EVERY allowed CPU, with the process pinned to that
single CPU for the duration (median of --reps reps kept, default 3):
  int     tight arithmetic loop, no allocation
  simd    bulk memcpy on 8 MiB blocks
  alloc   small-object churn (short strings/tuples) -- always pure Python
          on purpose: it mirrors the real dd-trace-py workload that found
          CPU 24
  fault   mmap fresh anonymous 1 MiB pages and touch every page
  stream  memcpy between buffers larger than L3
  latency pointer-chase through a shuffled table larger than L3
  gc      build a large cyclic object graph (payload strings like IAST
          taint ranges, cross-references) and time forced gc.collect() --
          always pure Python, same reason as alloc

int/simd/fault/stream/latency run in a checked-in C core (probe_native.c,
no dependencies) compiled on first use; the toolchain policy preflights
cc, then gcc, then clang, and if none exists tries ONE guarded
apt-get install of gcc before falling back. If no compiler can be had or
the build fails, the scenarios fall back to pure-Python cores and the
report records fidelity "fallback-python" per scenario (the latency
fallback is interpreter-bound and says so); the report's native.path
field records which path was taken (found / installed / cached /
fallback). alloc is always "python-workload".

Output (--out DIR, default ./probe-report):
  report.json  per-scenario host median, per-CPU deviation, flags,
               deviant CPUs, verdict, static host snapshot, per-source
               availability
  report.md    one row per CPU x scenario

Flag rule: a CPU deviates in a scenario when its median time per op
exceeds max(5%, 3x the median rep-to-rep spread) of the host median AND
every rep deviates from the host's rep-wise median in the same direction;
the direction clause keeps single-CPU flapping at the 5% boundary
unflagged unless it is really one-sided.

Best-effort everywhere: every failing source or mechanism is recorded
as unavailable; the script never raises and always writes its report.
Stdlib only.
"""

from __future__ import annotations

import argparse
from array import array
import ctypes
import gc
import json
import mmap as mmap_mod
import os
from pathlib import Path
import platform
import random
import shutil
import statistics
import subprocess
import sys
import time


SCENARIOS = ("int", "simd", "alloc", "fault", "stream", "latency", "gc")
NATIVE_SCENARIOS = ("int", "simd", "fault", "stream", "latency")
ALLOC = "alloc"  # always the pure-Python workload mirror
GC = "gc"  # same: the cyclic-GC graph chase mirrors the real workload
SCENARIO_UNITS = {
    "int": "iterations",
    "simd": "bytes",
    "alloc": "objects",
    "fault": "pages",
    "stream": "bytes",
    "latency": "hops",
    "gc": "collections",
}
DEFAULT_REPS = 3
DEFAULT_REP_SECONDS = 1.5
SIMD_BYTES = 8 << 20
FAULT_CHUNK = 1 << 20
PAGE = 4096
L3_FALLBACK_BYTES = 256 << 20
# the pure-Python latency fallback shuffles its table once; 64 MiB keeps
# that one-time cost to a few seconds while still exceeding any L3
LATENCY_FALLBACK_CAP = 64 << 20
# the gc scenario's live cyclic graph: 150k nodes with string payloads and
# 3-way cross-references is roughly 40 MB / 450k tracked objects, spanning
# well beyond L2 and around the judges' 37 MB L3, like a taint-object heap
GC_OBJECTS = 150_000
GC_PAYLOAD_CHARS = 32  # payload string length per node, like an IAST taint range
GC_CHURN_NODES = 512  # fresh cyclic garbage nodes per collection
GC_SEVER = 64  # live nodes whose refs are dropped and rebuilt per collection
FLAG_MIN_PCT = 5.0
FLAG_SPREAD_MULT = 3.0
# hard wall-clock cap on the sweep so the probe can never eat a CI job
# (7 scenarios x 24 CPUs x 3 reps x 1.5 s measured plus per-rep setup lands
# around 14 min; 16 min leaves margin while staying inside the 30 m job)
MAX_SWEEP_S = 16 * 60.0
NATIVE_BUILD_TIMEOUT_S = 60.0
# toolchain policy: preflight cc, then gcc, then clang; if none exists, ONE
# best-effort apt-get install of gcc (Linux only, never fatal) before falling
# back to the Python cores
APT_INSTALL_TIMEOUT_S = 120.0

_SOURCE_NATIVE = Path(__file__).with_name("probe_native.c")
_REPO_ROOT = Path(__file__).resolve().parents[3]  # steps -> benchmarks -> .gitlab -> repo
# built into the gitignored repo target dir so repeat runs reuse it
_NATIVE_BINARY = _REPO_ROOT / "target" / "probe_native"


def _read(path) -> str | None:
    try:
        return Path(path).read_text()
    except OSError:
        return None


def parse_cpulist(text: str) -> list:
    """Parse a kernel CPU list like "24-35,40" or "0-3" into sorted CPU ids."""
    cpus = []
    for part in text.strip().split(","):
        if not part:
            continue
        if "-" in part:
            start, end = part.split("-")
            try:
                cpus.extend(range(int(start), int(end) + 1))
            except ValueError:
                pass
        else:
            try:
                cpus.append(int(part))
            except ValueError:
                pass
    return sorted(set(cpus))


def parse_cache_size(text: str) -> int:
    """Parse a /sys cache size like "49152K" (or "1024M", plain bytes) to bytes."""
    t = text.strip()
    mult = 1
    if t and t[-1] in "KkMmGg":
        mult = {"k": 1024, "m": 1024**2, "g": 1024**3}[t[-1].lower()]
        t = t[:-1]
    try:
        return int(float(t) * mult)
    except ValueError:
        return 0


def read_l3_bytes(base=Path("/sys/devices/system/cpu/cpu0/cache")):
    """Total L3 bytes summed over cpu0's level-3 cache indexes; None if unreadable."""
    total = 0
    found = False
    for idx in sorted(Path(base).glob("index*")):
        level = _read(idx / "level")
        size = _read(idx / "size")
        if level is None or size is None:
            continue
        try:
            if int(level.strip()) == 3:
                total += parse_cache_size(size)
                found = True
        except ValueError:
            continue
    return total if found else None


def parse_cpuinfo_model(text: str):
    for line in text.splitlines():
        if line.startswith("model name"):
            return line.split(":", 1)[1].strip()
    return None


def parse_irq_effective_affinity(base=Path("/proc/irq")):
    """{irq: effective affinity list} from /proc/irq/*/effective_affinity_list.

    This is where the IRQs actually land, unlike smp_affinity_list which is
    only the allowed mask; the earlier watch could only capture the latter.
    """
    out = {}
    for irq_dir in sorted(Path(base).glob("[0-9]*")):
        aff = _read(irq_dir / "effective_affinity_list")
        if aff is not None:
            out[irq_dir.name] = aff.strip()
    return out


# -- pure-Python benchmark cores (fallbacks, plus the alloc mirror) ------


def py_int(seconds: float):
    m = (1 << 64) - 1
    x = 0x9E3779B97F4A7C15
    ops = 0
    start = time.monotonic()
    deadline = start + seconds
    while True:
        for _ in range(4096):
            x ^= (x << 13) & m
            x ^= x >> 7
            x ^= (x << 17) & m
        ops += 4096
        if time.monotonic() >= deadline:
            break
    return ops, time.monotonic() - start


def make_memcpy_bufs(size: int):
    """Pre-touched ctypes buffers for the memcpy cores; ctypes.memmove only
    takes ctypes instances or addresses on current Pythons.
    """
    src = ctypes.create_string_buffer(size)
    dst = ctypes.create_string_buffer(size)
    ctypes.memset(src, 0xA5, size)
    ctypes.memset(dst, 0, size)
    return src, dst


def py_memcpy(seconds: float, src, dst):
    n = len(src)
    move = ctypes.memmove
    ops = 0
    start = time.monotonic()
    deadline = start + seconds
    while True:
        move(dst, src, n)
        ops += n
        if time.monotonic() >= deadline:
            break
    return ops, time.monotonic() - start


def py_alloc(seconds: float):
    """Create and discard small strings/tuples, mirroring the path-heavy
    dd-trace-py microbenchmarks (ospathbasename_aspect) that found CPU 24.
    """
    ops = 0
    i = 0
    start = time.monotonic()
    deadline = start + seconds
    while True:
        for _ in range(256):
            s = "a/b/c/file%d.txt" % i
            name = s.rsplit("/", 1)[1]
            (s, name, (i, name))  # built and immediately discarded: the churn is the workload
            i += 1
        ops += 256
        if time.monotonic() >= deadline:
            break
    return ops, time.monotonic() - start


def py_fault(seconds: float):
    if not hasattr(mmap_mod, "mmap"):
        raise NotImplementedError("mmap unavailable on this platform")
    ops = 0
    start = time.monotonic()
    deadline = start + seconds
    while True:
        buf = mmap_mod.mmap(-1, FAULT_CHUNK)
        for off in range(0, FAULT_CHUNK, PAGE):
            buf[off] = 1
        buf.close()
        ops += FAULT_CHUNK // PAGE
        if time.monotonic() >= deadline:
            break
    return ops, time.monotonic() - start


def build_latency_table(bytes_: int):
    """Shuffled u32 permutation in an array (a superset of the C core's table)."""
    n = max(2, bytes_ // 4)
    tbl = array("I", range(n))
    random.Random(0xC0FFEE).shuffle(tbl)
    return tbl


def py_latency(seconds: float, tbl):
    idx = 0
    for _ in range(1 << 18):  # untimed warm-up, mirrors the C core
        idx = tbl[idx]
    ops = 0
    start = time.monotonic()
    deadline = start + seconds
    while True:
        for _ in range(256):
            idx = tbl[idx]
        ops += 256
        if time.monotonic() >= deadline:
            break
    return ops, time.monotonic() - start


# -- gc scenario: cyclic object graph + forced collection ------------------


class _GCNode:
    """One node of the cyclic graph: a payload string plus cross-references."""

    __slots__ = ("refs", "payload")

    def __init__(self, payload):
        self.payload = payload
        self.refs = ()


def _gc_payload(i: int) -> str:
    """Deterministic per-node payload, shaped like an IAST taint range."""
    return "range %d:%d = %s" % (i, (i * 31) % 4096, "taint"[i % 5] * GC_PAYLOAD_CHARS)


def build_gc_graph(n: int = GC_OBJECTS):
    """Build the live cyclic graph: n nodes, each with a payload and 3
    cross-references into pseudo-randomly spread peers, so pointer-chasing
    it during collection walks far beyond L2. Deterministic in n.
    """
    nodes = [_GCNode(_gc_payload(i)) for i in range(n)]
    for i, node in enumerate(nodes):
        node.refs = (nodes[(i * 7 + 3) % n], nodes[(i * 31 + 11) % n], nodes[(i + 5) % n])
    return nodes


def py_gc(seconds: float, n: int = GC_OBJECTS):
    """Time forced full collections over a large live cyclic graph.

    The graph is rebuilt the same way for every rep (outside the timed
    window); each timed iteration drops a rotating slice of references,
    adds a ring of fresh cyclic garbage that points back into the graph,
    rebuilds the slice, and forces gc.collect(). One op = one collection.
    """
    nodes = build_gc_graph(n)
    rng = random.Random(0x5EED)  # same churn sequence every rep
    ops = 0
    start = time.monotonic()
    deadline = start + seconds
    while True:
        # drop a rotating slice of live references
        base = (ops * 611) % n
        for k in range(GC_SEVER):
            nodes[(base + k) % n].refs = ()
        # fresh cyclic garbage: a ring of nodes referencing each other and
        # two live nodes each, unreachable as soon as the locals die
        first = prev = None
        for _ in range(GC_CHURN_NODES):
            node = _GCNode(_gc_payload(rng.randrange(n)))
            node.refs = (prev, nodes[rng.randrange(n)], nodes[rng.randrange(n)])
            if first is None:
                first = node
            prev = node
        first.refs = (prev, nodes[rng.randrange(n)])
        first = prev = node = None
        gc.collect()
        # rebuild the severed slice inside the timed window: the tuple
        # construction is part of the churn, like real taint objects
        for k in range(GC_SEVER):
            i = (base + k) % n
            nodes[i].refs = (nodes[(i * 7 + 3) % n], nodes[(i * 31 + 11) % n], nodes[(i + 5) % n])
        ops += 1
        if time.monotonic() >= deadline:
            break
    return ops, time.monotonic() - start


# -- native C core --------------------------------------------------------


class NativeError(Exception):
    pass


def _find_compiler():
    """Preflight the toolchain in policy order (cc, gcc, clang).

    Returns (compiler_name, first --version line) or (None, None).
    """
    for candidate in ("cc", "gcc", "clang"):
        try:
            found = subprocess.run([candidate, "--version"], capture_output=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if found.returncode == 0:
            return candidate, found.stdout.decode("utf-8", "replace").splitlines()[0]
    return None, None


def _install_gcc():
    """ONE cheap best-effort install of gcc; only on Linux with apt-get.

    Never fatal: any failure just means the caller falls back to the Python
    cores. Returns True only if apt-get reports success.
    """
    if not sys.platform.startswith("linux") or not shutil.which("apt-get"):
        return False
    try:
        proc = subprocess.run(
            ["apt-get", "install", "-y", "--no-install-recommends", "gcc"],
            capture_output=True,
            timeout=APT_INSTALL_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def compile_native(binary_path: Path = _NATIVE_BINARY, source: Path = _SOURCE_NATIVE):
    """Best-effort `cc -O2` build of the C core, following the toolchain policy.

    Preflight cc, then gcc, then clang; if none exists, try ONE apt-get install
    of gcc before falling back. Returns (binary_path_or_None, toolchain_or_None,
    path) where path records what happened for the report: "found:<cc>",
    "installed:<gcc>", "cached", or "fallback-python".
    """
    try:
        if binary_path.exists() and binary_path.stat().st_mtime >= source.stat().st_mtime:
            # cached binary; still preflight so the report shows the toolchain
            _cc, toolchain = _find_compiler()
            return binary_path, toolchain, "cached"
    except OSError:
        pass
    cc, toolchain = _find_compiler()
    path = "found:%s" % cc if cc is not None else None
    if cc is None and _install_gcc():
        cc, toolchain = _find_compiler()
        if cc is not None:
            path = "installed:%s" % cc
    if cc is None:
        return None, None, "fallback-python"
    try:
        binary_path.parent.mkdir(parents=True, exist_ok=True)
        built = subprocess.run(
            [cc, "-O2", "-o", str(binary_path), str(source)],
            capture_output=True,
            timeout=NATIVE_BUILD_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None, toolchain, path or "fallback-python"
    if built.returncode != 0 or not binary_path.exists():
        return None, toolchain, path or "fallback-python"
    return binary_path, toolchain, path


def run_native(binary, scenario: str, seconds: float, reps: int, size=None):
    """Run the C core once; returns [(ops, seconds), ...] one per rep."""
    cmd = [str(binary), scenario, repr(seconds), str(reps)]
    if size is not None:
        cmd.append(str(size))
    proc = subprocess.run(cmd, capture_output=True, timeout=seconds * reps * 8 + 60)
    if proc.returncode != 0:
        raise NativeError(
            "probe_native %s rc=%d: %s" % (scenario, proc.returncode, proc.stderr.decode("utf-8", "replace")[:200])
        )
    out = []
    for line in proc.stdout.decode("utf-8", "replace").splitlines():
        try:
            rec = json.loads(line)
            if rec.get("ops", 0) > 0 and rec.get("seconds", 0) > 0:
                out.append((int(rec["ops"]), float(rec["seconds"])))
        except (ValueError, TypeError, KeyError):
            continue
    if not out:
        raise NativeError("probe_native %s produced no usable output" % scenario)
    return out


# -- stats ----------------------------------------------------------------


def rep_times(reps):
    """seconds per op for each rep; [] if a rep produced no ops."""
    return [seconds / ops for ops, seconds in reps if ops > 0]


def spread_pct(times):
    if len(times) < 2:
        return 0.0
    med = statistics.median(times)
    return (max(times) - min(times)) / med * 100.0 if med else 0.0


def compute_scenario_stats(per_cpu: dict):
    """Host median, per-CPU deviation and flags for one scenario.

    per_cpu: {cpu_key: [seconds_per_op, ...]} for the CPUs that ran.
    A CPU is flagged when its median deviates from the host median by more
    than max(5%, 3x the median per-CPU rep-to-rep spread) AND every rep
    deviates from the host's rep-wise median in the same direction; the
    direction clause keeps threshold-boundary flapping unflagged.
    """
    stats = {"ran": bool(per_cpu), "cpus": {}}
    if len(per_cpu) < 2:
        for cpu, times in per_cpu.items():
            stats["cpus"][cpu] = {"median_s_per_op": statistics.median(times), "deviation_pct": None, "flagged": False}
        return stats
    medians = {cpu: statistics.median(times) for cpu, times in per_cpu.items()}
    host_median = statistics.median(medians.values())
    med_spread = statistics.median([spread_pct(t) for t in per_cpu.values()])
    threshold = max(FLAG_MIN_PCT, FLAG_SPREAD_MULT * med_spread)
    # host median per rep index, to judge a CPU's per-rep direction
    n_reps = max(len(t) for t in per_cpu.values())
    rep_medians = [statistics.median([t[r] for t in per_cpu.values() if len(t) > r]) for r in range(n_reps)]
    stats["host_median_s_per_op"] = host_median
    stats["rep_spread_pct"] = med_spread
    stats["flag_threshold_pct"] = threshold
    for cpu, med in medians.items():
        dev = (med - host_median) / host_median * 100.0
        deltas = [t - rep_medians[r] for r, t in enumerate(per_cpu[cpu]) if r < n_reps]
        consistent = all(d >= 0 for d in deltas) or all(d <= 0 for d in deltas)
        stats["cpus"][cpu] = {
            "median_s_per_op": med,
            "deviation_pct": round(dev, 2),
            "direction_consistent": consistent,
            "flagged": abs(dev) > threshold and consistent,
        }
    return stats


def decide_verdict(pin: dict, fidelity: dict, deviant_cpus: dict, notes: list) -> str:
    if not pin.get("pinned"):
        return "inconclusive"
    if any(f == "unavailable" for f in fidelity.values()):
        return "inconclusive"
    if any("budget" in n for n in notes):
        return "inconclusive"
    return "deviant" if deviant_cpus else "clean"


# -- probe ----------------------------------------------------------------


class Probe:
    def __init__(self, out_dir: Path, reps: int = DEFAULT_REPS, rep_seconds: float = DEFAULT_REP_SECONDS, cpus=None):
        self.out_dir = Path(out_dir)
        self.reps = reps
        self.rep_seconds = rep_seconds
        self.cpus_override = cpus
        self.sources = {}  # name -> "available" | "error: ..."
        self.notes = []
        self.native_binary = None
        self.native_info = {"toolchain": None, "build": "not attempted", "binary": None}
        self.fidelity = {}
        self.results = {
            s: {} for s in SCENARIOS
        }  # scenario -> cpu -> {"reps": [(ops, s)...]} | {"error"/"skipped": str}
        self.pin = {"pinned": False, "reason": "not run yet"}
        self.static = {}
        self.started = time.time()
        # working-set sizes, overridable for tests
        l3 = None
        try:
            l3 = read_l3_bytes()
        except Exception:
            pass
        self.l3 = l3
        self.stream_bytes = 2 * l3 if l3 else L3_FALLBACK_BYTES
        self.latency_bytes = self.stream_bytes
        self._simd_bufs = None
        self._stream_bufs = None
        self._latency_tbl = None

    # -- static snapshot --------------------------------------------------

    def _static_snapshot(self) -> dict:
        snap: dict = {"allowed_cpus": None}

        cpuinfo = _read("/proc/cpuinfo")
        model = parse_cpuinfo_model(cpuinfo) if cpuinfo is not None else None
        if model is None:
            model = self._sysctl_model() or platform.processor() or platform.machine()
            self.sources["cpu_model"] = "available" if model else "error: no source"
        else:
            self.sources["cpu_model"] = "available"
        snap["cpu_model"] = model

        topo = {}
        for cpu_dir in sorted(Path("/sys/devices/system/cpu").glob("cpu[0-9]*")):
            value = _read(cpu_dir / "topology" / "thread_siblings_list")
            if value is not None:
                topo[cpu_dir.name[3:]] = value.strip()
        snap["smt_siblings"] = topo
        self.sources["cpu_topology"] = "available" if topo else "error: /sys topology unreadable"

        nodes = {}
        for node_dir in sorted(Path("/sys/devices/system/node").glob("node*")):
            value = _read(node_dir / "cpulist")
            if value is not None:
                nodes[node_dir.name] = value.strip()
        snap["numa"] = nodes
        self.sources["numa"] = "available" if nodes else "error: /sys node unreadable"

        version = _read("/proc/version")
        snap["kernel"] = version.strip() if version is not None else " ".join(platform.uname())
        self.sources["kernel"] = "available" if version is not None else "error: /proc/version unreadable (uname used)"

        irq = parse_irq_effective_affinity()
        snap["irq_effective_affinity"] = irq
        self.sources["irq_effective_affinity"] = "available" if irq else "error: /proc/irq unreadable"

        per_cpu = any(_read(p) is not None for p in sorted(Path("/sys/kernel/irq").glob("*/per_cpu_count"))[:5])
        snap["irq_per_cpu_count"] = "available" if per_cpu else "unavailable"
        self.sources["irq_per_cpu_count"] = "available" if per_cpu else "error: /sys/kernel/irq unreadable"

        snap["l3_bytes"] = self.l3
        self.sources["sys_cache"] = "available" if self.l3 else "error: /sys cache info unreadable"

        try:
            snap["allowed_cpus"] = sorted(os.sched_getaffinity(0))
            self.sources["affinity_api"] = "available"
        except (AttributeError, OSError) as exc:
            self.sources["affinity_api"] = "error: %s" % exc
        return snap

    @staticmethod
    def _sysctl_model():
        try:
            out = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, timeout=5)
            if out.returncode == 0:
                return out.stdout.decode("utf-8", "replace").strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
        return None

    # -- cpu set ----------------------------------------------------------

    def resolve_cpus(self) -> list:
        if self.cpus_override is not None:
            return list(self.cpus_override)
        allowed = self.static.get("allowed_cpus") or []
        if not allowed:
            self.pin = {"pinned": False, "reason": "sched_getaffinity unavailable or empty"}
            return []
        return allowed

    # -- native build -----------------------------------------------------

    def _build_native(self) -> None:
        binary, toolchain, path = compile_native()
        self.native_info = {"toolchain": toolchain, "binary": str(binary) if binary else None, "path": path}
        if binary is not None:
            self.native_binary = binary
            self.native_info["build"] = "ok"
            self.sources["native_build"] = "available"
        else:
            if path == "fallback-python":
                self.native_info["build"] = "no compiler after preflight (cc, gcc, clang) and install attempt"
            else:
                self.native_info["build"] = "failed; using Python fallbacks"
            self.sources["native_build"] = "error: %s" % self.native_info["build"]
        for scenario in NATIVE_SCENARIOS:
            self.fidelity[scenario] = "native" if self.native_binary else "fallback-python"
        self.fidelity[ALLOC] = "python-workload"
        self.fidelity[GC] = "python-workload"
        if self.native_binary is None:
            self.notes.append("native core unavailable: int/simd/fault/stream/latency use fallback-python")

    # -- scenario execution -----------------------------------------------

    def _ensure_buffers(self, scenario):
        if scenario == "simd":
            if self._simd_bufs is None:
                self._simd_bufs = make_memcpy_bufs(SIMD_BYTES)
            return self._simd_bufs
        if scenario == "stream":
            if self._stream_bufs is None:
                self._stream_bufs = make_memcpy_bufs(self.stream_bytes)
            return self._stream_bufs
        if scenario == "latency":
            if self._latency_tbl is None:
                self._latency_tbl = build_latency_table(min(self.latency_bytes, LATENCY_FALLBACK_CAP))
            return self._latency_tbl
        return None

    def _python_core(self, scenario):
        if scenario == "int":
            return py_int(self.rep_seconds)
        if scenario == "alloc":
            return py_alloc(self.rep_seconds)
        if scenario == "fault":
            return py_fault(self.rep_seconds)
        if scenario == "simd":
            return py_memcpy(self.rep_seconds, *self._ensure_buffers("simd"))
        if scenario == "stream":
            return py_memcpy(self.rep_seconds, *self._ensure_buffers("stream"))
        if scenario == "latency":
            return py_latency(self.rep_seconds, self._ensure_buffers("latency"))
        if scenario == "gc":
            return py_gc(self.rep_seconds)
        raise ValueError("unknown scenario %s" % scenario)

    def _run_scenario(self, scenario):
        """One scenario, --reps reps, on the current (pinned) CPU.

        Returns {"reps": [(ops, seconds), ...]}; raises only if the scenario
        is unavailable on this platform.
        """
        if scenario in NATIVE_SCENARIOS and self.native_binary:
            size = None
            if scenario == "simd":
                size = SIMD_BYTES
            elif scenario == "stream":
                size = self.stream_bytes
            elif scenario == "latency":
                size = self.latency_bytes
            try:
                return {"reps": run_native(self.native_binary, scenario, self.rep_seconds, self.reps, size)}
            except (NativeError, OSError, subprocess.TimeoutExpired) as exc:
                self.fidelity[scenario] = "fallback-python"
                self.notes.append("%s: native run failed (%s), using fallback-python" % (scenario, exc))
        return {"reps": [self._python_core(scenario) for _ in range(self.reps)]}

    # -- sweep ------------------------------------------------------------

    def sweep(self, cpus: list) -> None:
        if not cpus:
            self.pin = {"pinned": False, "reason": self.pin.get("reason", "no allowed CPUs")}
            for scenario in SCENARIOS:
                try:
                    self.results[scenario]["unpinned"] = self._run_scenario(scenario)
                except Exception as exc:  # noqa: BLE001 - never fatal
                    self.fidelity[scenario] = "unavailable"
                    self.results[scenario]["unpinned"] = {"error": repr(exc)}
                    self.notes.append("%s unavailable: %r" % (scenario, exc))
            return
        original = None
        try:
            original = set(os.sched_getaffinity(0))
        except (AttributeError, OSError):
            pass
        probed = []
        failed_pins = []
        deadline = time.monotonic() + MAX_SWEEP_S
        budget_hit = False
        for cpu in cpus:
            if time.monotonic() > deadline:
                budget_hit = True
                self.notes.append("budget: sweep stopped before CPU %d (max %ds)" % (cpu, MAX_SWEEP_S))
                continue
            try:
                os.sched_setaffinity(0, {cpu})
            except (AttributeError, OSError) as exc:
                failed_pins.append(cpu)
                for scenario in SCENARIOS:
                    self.results[scenario][str(cpu)] = {"skipped": "pin failed: %s" % exc}
                continue
            probed.append(cpu)
            for scenario in SCENARIOS:
                try:
                    self.results[scenario][str(cpu)] = self._run_scenario(scenario)
                except Exception as exc:  # noqa: BLE001 - never fatal
                    self.fidelity[scenario] = "unavailable"
                    self.results[scenario][str(cpu)] = {"error": repr(exc)}
                    self.notes.append("%s unavailable on CPU %d: %r" % (scenario, cpu, exc))
        if original is not None:
            try:
                os.sched_setaffinity(0, original)
            except OSError:
                pass
        self.pin = {
            "pinned": bool(probed),
            "allowed_cpus": list(cpus),
            "probed_cpus": probed,
            "pin_failed_cpus": failed_pins,
        }
        if failed_pins:
            self.notes.append("pin failed on CPUs %s" % failed_pins)
        if budget_hit and probed:
            self.notes.append("budget: %d CPUs skipped" % (len(cpus) - len(probed) - len(failed_pins)))

    # -- report -----------------------------------------------------------

    def _scenario_stats(self, scenario):
        per_cpu = {}
        for cpu, entry in self.results[scenario].items():
            if isinstance(entry, dict) and entry.get("reps"):
                times = rep_times(entry["reps"])
                if times:
                    per_cpu[cpu] = times
        stats = compute_scenario_stats(per_cpu)
        stats["fidelity"] = self.fidelity.get(scenario)
        stats["unit"] = SCENARIO_UNITS[scenario]
        if scenario == "latency" and stats["fidelity"] == "fallback-python":
            stats["note"] = "interpreter-bound"
        for cpu, entry in self.results[scenario].items():
            if "error" in (entry or {}):
                stats.setdefault("errors", {})[cpu] = entry["error"]
            elif "skipped" in (entry or {}):
                stats.setdefault("skipped", {})[cpu] = entry["skipped"]
            elif "reps" in (entry or {}) and cpu in stats["cpus"]:
                stats["cpus"][cpu]["ops_per_s"] = [round(ops / seconds, 1) for ops, seconds in entry["reps"]]
        return stats

    def build_report(self):
        scenarios = {s: self._scenario_stats(s) for s in SCENARIOS}
        deviant = {}
        for scenario, stats in scenarios.items():
            for cpu, entry in stats.get("cpus", {}).items():
                if entry.get("flagged"):
                    deviant.setdefault(cpu, []).append({"scenario": scenario, "deviation_pct": entry["deviation_pct"]})
        for scenario in SCENARIOS:
            if not self.results[scenario] or all(
                isinstance(e, dict) and ("error" in e or "skipped" in e) for e in self.results[scenario].values()
            ):
                if self.fidelity.get(scenario) != "unavailable":
                    self.fidelity[scenario] = "unavailable"
                    self.notes.append("%s: no usable results" % scenario)
        verdict = decide_verdict(self.pin, self.fidelity, deviant, self.notes)
        return {
            "meta": {
                "probe": "cpu-asymmetry probe (APMSP-4059)",
                "started": self.started,
                "finished": time.time(),
                "reps": self.reps,
                "rep_seconds": self.rep_seconds,
                "tier": "ci" if (os.environ.get("CI") or os.environ.get("GITLAB_CI")) else "local",
                "platform": platform.platform(),
                "native": self.native_info,
                "pin": self.pin,
                "notes": self.notes,
            },
            "sources": self.sources,
            "static": self.static,
            "scenarios": scenarios,
            "deviant_cpus": deviant,
            "verdict": verdict,
        }

    def write_reports(self, report):
        self.out_dir.mkdir(parents=True, exist_ok=True)
        (self.out_dir / "report.json").write_text(json.dumps(report, indent=1) + "\n")
        (self.out_dir / "report.md").write_text(render_markdown(report))

    # -- main -------------------------------------------------------------

    def run(self):
        try:
            self.static = self._static_snapshot()
        except Exception as exc:  # noqa: BLE001 - never fatal
            self.notes.append("static snapshot failed: %r" % exc)
        cpus = []
        try:
            cpus = self.resolve_cpus()
        except Exception as exc:  # noqa: BLE001
            self.notes.append("cpu resolution failed: %r" % exc)
            self.pin = {"pinned": False, "reason": repr(exc)}
        try:
            self._build_native()
        except Exception as exc:  # noqa: BLE001
            for scenario in SCENARIOS:
                self.fidelity[scenario] = "fallback-python" if scenario not in (ALLOC, GC) else "python-workload"
            self.notes.append("native build crashed: %r" % exc)
        try:
            self.sweep(cpus)
        except Exception as exc:  # noqa: BLE001
            self.notes.append("sweep failed: %r" % exc)
            self.pin = {"pinned": False, "reason": "sweep crashed: %r" % exc}
        report = self.build_report()
        self.write_reports(report)
        return report


def _fmt_time(t):
    """Format seconds-per-op in a human unit."""
    if t >= 1e-3:
        return "%.3f ms" % (t * 1e3)
    if t >= 1e-6:
        return "%.3f us" % (t * 1e6)
    return "%.3f ns" % (t * 1e9)


def render_markdown(report):
    meta = report["meta"]
    static = report.get("static", {})
    lines = [
        "# CPU-asymmetry probe report",
        "",
        "- verdict: **%s**" % report["verdict"],
        "- cpu: %s | kernel: %s | platform: %s | tier: %s"
        % (static.get("cpu_model"), static.get("kernel"), meta.get("platform"), meta.get("tier")),
        "- allowed CPUs: %s | probed: %s" % (static.get("allowed_cpus"), meta["pin"].get("probed_cpus")),
        "- pin: %s" % json.dumps(meta["pin"]),
        "- native core: %s | L3: %s bytes" % (meta["native"].get("build"), static.get("l3_bytes")),
        "- reps: %s x %ss per scenario and CPU" % (meta["reps"], meta["rep_seconds"]),
    ]
    deviant = report["deviant_cpus"]
    if deviant:
        lines.append("- deviant CPUs:")
        for cpu, flagged in sorted(deviant.items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else 0):
            parts = ", ".join("%s %+0.1f%%" % (f["scenario"], f["deviation_pct"]) for f in flagged)
            lines.append("  - CPU %s: %s" % (cpu, parts))
    else:
        lines.append("- deviant CPUs: none")
    if meta.get("notes"):
        lines.append("- notes: %s" % "; ".join(meta["notes"]))
    lines += ["", "| CPU | scenario | fidelity | time/op | deviation | flagged |", "|---|---|---|---|---|---|"]
    for scenario, stats in report["scenarios"].items():
        fid = stats.get("fidelity", "?")
        for cpu, entry in sorted(stats.get("cpus", {}).items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else 0):
            dev = entry.get("deviation_pct")
            lines.append(
                "| %s | %s | %s | %s | %s | %s |"
                % (
                    cpu,
                    scenario,
                    fid,
                    _fmt_time(entry.get("median_s_per_op", float("nan"))),
                    "%+0.1f%%" % dev if dev is not None else "-",
                    "**FLAG**" if entry.get("flagged") else "",
                )
            )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="per-CPU microbenchmarks that flag asymmetric cores")
    parser.add_argument("--out", default="./probe-report", help="output directory (default ./probe-report)")
    parser.add_argument("--reps", type=int, default=DEFAULT_REPS, help="reps per CPU and scenario (default 3)")
    parser.add_argument(
        "--rep-seconds", type=float, default=DEFAULT_REP_SECONDS, help="measured seconds per rep (default 1.5)"
    )
    args = parser.parse_args()
    probe = Probe(Path(args.out), reps=args.reps, rep_seconds=args.rep_seconds)
    report = probe.run()
    print("verdict: %s" % report["verdict"])
    print("report: %s" % (probe.out_dir / "report.json"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
