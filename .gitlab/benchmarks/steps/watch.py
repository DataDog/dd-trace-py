#!/usr/bin/env python3
"""CPU watch: passive per-core/device counter sampler around a benchmark command.

EXPERIMENT (do not merge): CPU-asymmetry probe on benchmarking hosts
(PR #20052 / APMSP-4059). Runs alongside the microbenchmark harness and
records, at 1 Hz, what every CPU and device is doing, so each config's
candidate/baseline delta can be joined with the counters of the cores and
time window it ran on. See the spec card and lab notebook in the vault.

Usage: watch.py --out DIR -- CMD [ARGS...]

Output (under DIR):
  static.json      one-time snapshot: topology, NUMA, IRQ affinities (allowed
                   AND effective) with device names, per-IRQ per-CPU counts
                   (/sys/kernel/irq, the unmasked stand-in for /proc/interrupts),
                   cpufreq policy, kernel, cgroup cpuset, allowed CPUs,
                   per-source {source, available, error} records, why
                   /proc/interrupts reads empty (diagnosis only), perf/MSR
                   preflight
  samples.jsonl.gz 1 Hz samples: per-CPU /proc/stat, /proc/interrupts,
                   /proc/softirqs, /proc/schedstat (run-queue wait),
                   /proc/<pid>/schedstat (per-process run_delay) and
                   /proc/<pid>/status context switches, per-IRQ per-CPU
                   counts, per-CPU irq:irq_handler_entry counter, PSI,
                   selected vmstat, numastat, thermal throttle counts,
                   per-CPU frequency, per-process stat
  end.json         end-of-run snapshot: IRQ effective affinities re-read, final
                   per-CPU irq:irq_handler_entry counts, SMI counts (start and
                   end), per-source {source, available, error} records
  meta.json        run summary: pinning decision, own CPU time, read errors

Self-intrusion controls:
  - Pins itself (all threads) to allowed CPUs outside the benchmark ranges,
    discovered from the affinity of taskset-pinned descendants; records the
    decision when pinning isn't possible.
  - Records its own CPU time; per-process samples include the watch itself.
  - Generates no load beyond reading /proc and /sys at 1 Hz and reading its
    own counter file descriptors. T6 additions: it holds two read-only
    per-CPU counting events open for its lifetime (irq:irq_handler_entry and
    msr/smi via perf_event_open, so /proc/interrupts being masked does not
    blind the probe) and may read MSR_SMI_COUNT (0x34) from /dev/cpu/N/msr.
    These are pure syscall attempts: under perf_event_paranoid=2 without
    capabilities they fail with EACCES and that is recorded. Every probe and
    source read is best-effort, recorded as {source, available, error}, and
    none can fail the job. The watch never mounts or umounts anything: the
    runtime's /proc/interrupts mask is deliberate, and the watch only
    diagnoses it and reads the unmasked equivalents instead.

Stdlib only; the samples file is gzip JSONL, one JSON object per line with an
"t" epoch-seconds key that joins with placement.jsonl timestamps from the
harness's run.py.
"""

from __future__ import annotations

import argparse
import ctypes
import gzip
import json
import os
from pathlib import Path
import platform
import re
import shutil
import struct
import subprocess
import sys
import threading
import time


SAMPLE_INTERVAL_S = 1.0
# How many ticks to keep looking for pinned descendants before giving up on
# self-pinning (the harness tasksets its benchmark runs within a few seconds).
PIN_DISCOVERY_TICKS = 30

# /proc/vmstat keys worth keeping, by prefix. NUMA hit/miss and fault/reclaim
# counters cover the memory-side hypotheses; the rest contextualize PSI.
VMSTAT_PREFIXES = (
    "pgfault",
    "pgmajfault",
    "pswpin",
    "pswpout",
    "pgalloc",
    "pgfree",
    "pgrefill",
    "pgscan",
    "pgsteal",
    "numa_",
    "compact_",
    "thp_",
    "workingset_",
)

_CPU_SYS = Path("/sys/devices/system/cpu")
_NODE_SYS = Path("/sys/devices/system/node")
# /sys/devices/system/node/nodeN/numastat pairs, single- or multi-space
# separated (the old aligned-column layout and the new one-per-line layout)
_NUMA_STAT_PAIR_RE = re.compile(r"([a-z_]+)\s+(\d+)")


def parse_cpulist(text: str) -> list:
    """Parse a kernel CPU list like "24-35,40" or "0-3" into sorted CPU ids."""
    cpus: list[int] = []
    for part in text.strip().split(","):
        if not part:
            continue
        if "-" in part:
            start, end = part.split("-")
            cpus.extend(range(int(start), int(end) + 1))
        else:
            cpus.append(int(part))
    return sorted(cpus)


def read_stat(text: str) -> dict:
    """Per-CPU /proc/stat fields (user nice system idle iowait irq softirq
    steal guest guest_nice) plus totals "intr" and "ctxt".
    """
    out: dict[str, object] = {}
    for line in text.splitlines():
        if line.startswith("intr ") or line.startswith("ctxt "):
            key, _, value = line.partition(" ")
            # "intr" is a total followed by per-IRQ counts; only the total
            # matters for the timeline
            out[key] = int(value.split()[0])
        elif line.startswith("cpu"):
            fields = line.split()
            # keep "cpu" (aggregate) and "cpuN" rows, skip a bare "cpu"
            if len(fields) > 1 and (fields[0] == "cpu" or fields[0][3:].isdigit()):
                out[fields[0]] = [int(f) for f in fields[1:]]
    return out


def read_interrupts(text: str) -> dict:
    """Rows of /proc/interrupts: per-CPU counts (tokens 1..ncpu) plus the
    trailing device name. Rows carrying only a grand total and no
    per-CPU counts are skipped.
    """
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return {}
    cpus = [c for c in lines[0].split() if c.startswith("CPU")]
    ncpu = len(cpus)
    rows: dict[str, dict] = {}
    for line in lines[1:]:
        tokens = line.split()
        if len(tokens) < ncpu + 2:
            continue
        key = tokens[0].rstrip(":")
        try:
            counts = [int(t) for t in tokens[1 : 1 + ncpu]]
        except ValueError:
            continue
        rows[key] = {"dev": " ".join(tokens[1 + ncpu :]), "counts": counts}
    return {"cpus": cpus, "rows": rows}


def read_softirqs(text: str) -> dict:
    """Rows (HI, TIMER, NET_RX, ...) with per-CPU counts; the CPU header line
    has no trailing colon and is skipped naturally.
    """
    rows: dict[str, list] = {}
    for line in text.splitlines():
        tokens = line.split()
        if not tokens or not tokens[0].endswith(":"):
            continue
        try:
            rows[tokens[0].rstrip(":")] = [int(t) for t in tokens[1:]]
        except ValueError:
            continue
    return rows


def read_psi(text: str) -> dict:
    """One /proc/pressure/<res> file: {"some": {avg10.., total}, "full": ...}."""
    out: dict[str, dict] = {}
    for line in text.splitlines():
        kind, _, rest = line.partition(" ")
        fields = {}
        for item in rest.split():
            key, _, value = item.partition("=")
            try:
                fields[key] = float(value)
            except ValueError:
                pass
        out[kind] = fields
    return out


def read_vmstat(text: str) -> dict:
    """/proc/vmstat filtered to VMSTAT_PREFIXES (faults, reclaim, NUMA hits)."""
    out: dict[str, int] = {}
    for line in text.splitlines():
        key, _, value = line.partition(" ")
        if key.startswith(VMSTAT_PREFIXES):
            try:
                out[key] = int(value)
            except ValueError:
                pass
    return out


def read_numastat(text: str) -> dict:
    """One /sys/devices/system/node/nodeN/numastat file (old and new layouts)."""
    return {m.group(1): int(m.group(2)) for m in _NUMA_STAT_PAIR_RE.finditer(text)}


def read_proc_pid_stat(text: str) -> list:
    """One /proc/<pid>/stat line as [comm, ppid, utime, stime, processor].

    The comm field is parenthesized and may contain spaces or ')' itself, so
    the line is split on the last ')' only.
    """
    pid_part, _, rest = text.rpartition(")")
    fields = rest.split()
    # stat fields after the comm: state(0) ppid(1) ... utime(11) stime(12)
    # ... processor(36); indices relative to the post-')' split.
    return [pid_part.split("(", 1)[1] or "", int(fields[1]), int(fields[11]), int(fields[12]), int(fields[36])]


def read_schedstat(text: str) -> dict:
    """Rows of /proc/schedstat: "cpuN <ints...>". The column meanings moved
    across kernel versions (on current kernels the second field is the
    runqueue's accumulated run_delay in ns), so the parser stays
    layout-agnostic and the readout picks the column.
    """
    rows: dict[str, list] = {}
    for line in text.splitlines():
        tokens = line.split()
        if len(tokens) < 2 or not tokens[0].startswith("cpu") or not tokens[0][3:].isdigit():
            continue
        try:
            rows[tokens[0][3:]] = [int(t) for t in tokens[1:]]
        except ValueError:
            continue
    return rows


def read_pid_schedstat(text: str) -> list:
    """One /proc/<pid>/schedstat line: time-on-cpu (ns), time waiting on the
    runqueue i.e. run_delay (ns), timeslices run.
    """
    return [int(t) for t in text.split()]


def read_pid_status_ctxt(text: str) -> dict:
    """The two context-switch counters of /proc/<pid>/status: voluntary and
    nonvoluntary. Nonvoluntary growth on a benchmark CPU means the scheduler
    preempted it for something else (H6).
    """
    out = {}
    for line in text.splitlines():
        if line.startswith("voluntary_ctxt_switches:"):
            try:
                out["voluntary"] = int(line.split("\t", 1)[1])
            except (IndexError, ValueError):
                pass
        elif line.startswith("nonvoluntary_ctxt_switches:"):
            try:
                out["nonvoluntary"] = int(line.split("\t", 1)[1])
            except (IndexError, ValueError):
                pass
    return out


def read_irq_per_cpu_count(text: str) -> list:
    """One /sys/kernel/irq/<N>/per_cpu_count line: "N,N,N,..." with one
    cumulative count per possible CPU (this is the unmasked stand-in for
    /proc/interrupts). Entries that are not plain integers (offline CPUs
    show blank on some kernels) parse to None.
    """
    values = []
    for token in text.replace("\n", "").split(","):
        token = token.strip()
        try:
            values.append(int(token))
        except ValueError:
            values.append(None)
    return values


def read_cpuinfo(text: str) -> list:
    """/proc/cpuinfo condensed to per-processor model, microcode and MHz."""
    procs = []
    current: dict[str, object] = {}
    for line in text.splitlines():
        if not line.strip():
            if current:
                procs.append(current)
                current = {}
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        if key == "processor":
            current = {"processor": int(value.strip())}
        elif key in ("model name", "microcode", "cpu MHz"):
            current[key] = value.strip()
    if current:
        procs.append(current)
    return procs


def read_cpuinfo_mhz(text: str) -> dict:
    """Per-processor current MHz from /proc/cpuinfo (the kernel's frequency
    estimate, updated from APERF/MPERF -- readable where scaling_cur_freq is
    not).
    """
    mhz = {}
    current = None
    for line in text.splitlines():
        if line.startswith("processor"):
            _, _, value = line.partition(":")
            current = value.strip()
        elif line.startswith("cpu MHz") and current is not None:
            try:
                mhz[current] = float(line.partition(":")[2])
            except ValueError:
                pass
    return mhz


def _read(path: Path):
    try:
        return path.read_text()
    except OSError:
        return None


def source_probe(path) -> dict:
    """Best-effort readability record for one source: {source, available,
    error}. Never raises; a source that reads back empty counts as not
    available (the containers mask /proc/interrupts that way).
    """
    source = str(path)
    try:
        text = Path(path).read_text()
    except OSError as exc:
        return {"source": source, "available": False, "error": "%s: %s" % (type(exc).__name__, exc)}
    if not text.strip():
        return {"source": source, "available": False, "error": "empty"}
    return {"source": source, "available": True, "error": None}


def sample_procs() -> dict:
    """Per-process stat, schedstat and context switches for every
    /proc-visible pid. The stat fields (comm, ppid, utime, stime, last CPU)
    let the readout split a core's busy time into "ours" vs "someone else's"
    (H6); the schedstat triple gives the benchmark processes' own run_delay
    and the status counters their preemptions (T6). Pids whose schedstat or
    status is missing or unreadable simply have no entry there.
    """
    procs: dict[str, list] = {}
    sched: dict[str, list] = {}
    ctxt: dict[str, dict] = {}
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        base = Path("/proc") / entry
        text = _read(base / "stat")
        if text is None:
            continue
        try:
            procs[entry] = read_proc_pid_stat(text)
        except (IndexError, ValueError):
            continue
        stext = _read(base / "schedstat")
        if stext is not None:
            try:
                sched[entry] = read_pid_schedstat(stext)
            except ValueError:
                pass
        utext = _read(base / "status")
        if utext is not None:
            entry_ctxt = read_pid_status_ctxt(utext)
            if entry_ctxt:
                ctxt[entry] = entry_ctxt
    return {"procs": procs, "sched": sched, "ctxt": ctxt}


# -- passive perf counters and best-effort source probes (T6) --------------

PERF_TYPE_HARDWARE = 0
PERF_TYPE_SOFTWARE = 1
PERF_TYPE_TRACEPOINT = 2
PERF_COUNT_SW_TASK_CLOCK = 1
MSR_SMI_COUNT = 0x34
PERF_EVENT_OPEN_SYSCALL = {"x86_64": 298}

# attr.flags bits from perf_event_open(2)
_BIT_INHERIT = 1 << 1
_BIT_EXCLUDE_KERNEL = 1 << 4
# read_format bits from perf_event_open(2)
_READ_TIME_ENABLED = 1
_READ_TIME_RUNNING = 2


class PerfEventAttr(ctypes.Structure):
    """perf_event_attr truncated after config1 (64 bytes = PERF_ATTR_SIZE_VER0,
    the v2.6.32 layout): counting-only events set no field beyond it, but the
    kernel rejects attr sizes smaller than VER0 with E2BIG, so the struct
    must be exactly 64 bytes, not shorter (sizes larger than the kernel knows
    are the direction that is versioned forward).
    """

    _fields_ = [
        ("type", ctypes.c_uint),
        ("size", ctypes.c_uint),
        ("config", ctypes.c_ulonglong),
        ("sample_period", ctypes.c_ulonglong),
        ("sample_type", ctypes.c_ulonglong),
        ("read_format", ctypes.c_ulonglong),
        ("flags", ctypes.c_ulonglong),
        ("wakeup", ctypes.c_uint),
        ("bp_type", ctypes.c_uint),
        ("config1", ctypes.c_ulonglong),
    ]

    def __init__(self, type_, config, inherit=False, exclude_kernel=False, read_format=0):
        super().__init__()
        self.type = type_
        self.size = ctypes.sizeof(PerfEventAttr)
        self.config = config
        flags = 0
        if inherit:
            flags |= _BIT_INHERIT
        if exclude_kernel:
            flags |= _BIT_EXCLUDE_KERNEL
        self.flags = flags
        self.read_format = read_format


_libc: ctypes.CDLL | None = None


def perf_event_open(attr: PerfEventAttr, pid: int, cpu: int) -> int:
    """Open a counting event via the raw perf_event_open syscall (2), without
    libperf; raises OSError on failure so callers can record it and continue.
    """
    if not sys.platform.startswith("linux"):
        raise OSError("perf_event_open: not Linux (%r)" % sys.platform)
    nr = PERF_EVENT_OPEN_SYSCALL.get(platform.machine())
    if nr is None:
        raise OSError("perf_event_open: no syscall number for %r" % platform.machine())
    global _libc
    if _libc is None:
        _libc = ctypes.CDLL(None, use_errno=True)
    rc = _libc.syscall(
        ctypes.c_long(nr),
        ctypes.byref(attr),
        ctypes.c_long(pid),
        ctypes.c_long(cpu),
        ctypes.c_long(-1),
        ctypes.c_ulong(0),
    )
    if rc < 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err))
    return rc


def read_perf_counter(fd: int) -> int:
    """Current value of a plain (read_format=0) counting event."""
    data = os.read(fd, 8)
    if len(data) != 8:
        raise OSError("short counter read: %d bytes" % len(data))
    return struct.unpack("<Q", data)[0]


_PMU_DEVICES = Path("/sys/bus/event_source/devices")
_TRACING_BASES = (Path("/sys/kernel/tracing"), Path("/sys/kernel/debug/tracing"))
_FORMAT_TERM_RE = re.compile(r"config:(\d+)-(\d+)$")


def tracepoint_id(name: str, bases=_TRACING_BASES) -> int | None:
    """Numeric id of a tracepoint like "irq/irq_handler_entry" from tracingfs;
    None when tracingfs is not mounted or the event is absent.
    """
    for base in bases:
        text = _read(base / "events" / name / "id")
        if text is not None:
            try:
                return int(text.strip())
            except ValueError:
                return None
    return None


def pmu_event_config(pmu: str, event: str, devices: Path = _PMU_DEVICES) -> tuple[int, int] | None:
    """(type, config) for a sysfs PMU event like msr/smi, assembled from the
    event file's term=value pairs and the PMU's format masks; None when the
    PMU, the event or a needed format file is missing or uses a layout this
    parser does not handle.
    """
    base = devices / pmu
    etext = _read(base / "events" / event)
    if etext is None:
        return None
    config = 0
    for term in etext.strip().split(","):
        key, _, value = term.partition("=")
        fmt = _read(base / "format" / key)
        if fmt is None:
            return None
        m = _FORMAT_TERM_RE.match(fmt.strip())
        if m is None:
            return None  # only whole-value config layouts (msr and friends)
        lo, hi = int(m.group(1)), int(m.group(2))
        try:
            number = int(value, 0)
        except ValueError:
            return None
        config |= (number << lo) & (((1 << (hi - lo + 1)) - 1) << lo)
    ttext = _read(base / "type")
    if ttext is None:
        return None
    try:
        return int(ttext.strip()), config
    except ValueError:
        return None


def read_msr_smi_count(cpus: list) -> dict | None:
    """Absolute SMI counts per CPU from MSR 0x34 (/dev/cpu/N/msr), or None
    when the msr character devices are not usable in this container.
    """
    values: dict[int, int] = {}
    try:
        for cpu in cpus:
            fd = os.open("/dev/cpu/%d/msr" % cpu, os.O_RDONLY)
            try:
                data = os.pread(fd, 8, MSR_SMI_COUNT)
                if len(data) != 8:
                    return None
                values[cpu] = struct.unpack("<Q", data)[0]
            finally:
                os.close(fd)
    except OSError:
        return None
    return values


class PassivePerfCounters:
    """Read-only per-CPU counting events held open for the watch's lifetime:
    irq:irq_handler_entry (per-CPU hardware-IRQ entry counts -- the fallback
    for /proc/interrupts being masked) and msr/smi (SMI deltas). Every open or
    read failure is recorded in `status` verbatim and degrades to an empty
    result; nothing here can fail the job.
    """

    def __init__(self, cpus: list):
        self.cpus = list(cpus)
        self.status: dict[str, str] = {}
        self.last_error = ""
        self.irq_fds: dict[int, int] = {}
        tp = tracepoint_id("irq/irq_handler_entry")
        if tp is None:
            self.status["irq_entry"] = "tracepoint id not found (tracingfs not mounted?)"
        elif not self.cpus:
            self.status["irq_entry"] = "no allowed CPUs"
        else:
            self.irq_fds = self._open_per_cpu(PERF_TYPE_TRACEPOINT, tp)
            if not self.irq_fds:
                self.status["irq_entry"] = "perf_event_open cpu-wide failed: " + self.last_error
        self.smi_method = "unavailable"
        self.smi_fds: dict[int, int] = {}
        self.smi_start: dict[int, int] = {}
        self._init_smi()

    def _open_per_cpu(self, ptype: int, config: int) -> dict:
        """One cpu-wide counting event fd per CPU; {} (and last_error set) on
        the first failure, closing what was opened.
        """
        fds: dict[int, int] = {}
        for cpu in self.cpus:
            try:
                fds[cpu] = perf_event_open(PerfEventAttr(ptype, config), -1, cpu)
            except OSError as exc:
                self.last_error = "cpu %d: %s" % (cpu, exc)
                for fd in fds.values():
                    os.close(fd)
                return {}
        return fds

    def _init_smi(self) -> None:
        direct = read_msr_smi_count(self.cpus)
        if direct is not None:
            # absolute counts; re-read at the end for the delta
            self.smi_method = "msr0x34"
            self.smi_start = direct
            return
        cfg = pmu_event_config("msr", "smi")
        if cfg is None:
            self.status["smi"] = "msr PMU smi event not found"
            return
        fds = self._open_per_cpu(cfg[0], cfg[1])
        if not fds:
            self.status["smi"] = "perf_event_open cpu-wide failed: " + self.last_error
            return
        try:
            self.smi_start = {cpu: read_perf_counter(fd) for cpu, fd in fds.items()}
        except OSError as exc:
            self.status["smi"] = "counter read failed: %s" % exc
            for fd in fds.values():
                os.close(fd)
            return
        self.smi_method = "msr_pmu"
        self.smi_fds = fds

    def sample(self) -> dict:
        """{cpu: cumulative irq:irq_handler_entry count} for the 1 Hz timeline."""
        counts: dict[str, int] = {}
        for cpu, fd in self.irq_fds.items():
            try:
                counts[str(cpu)] = read_perf_counter(fd)
            except OSError:
                # a transient read error leaves the CPU out of this tick; the
                # final read in finish() records a persistent one
                continue
        return counts

    def finish(self) -> dict:
        """Serializable end state: final per-CPU IRQ counts and the SMI story;
        closes every fd.
        """
        out: dict[str, object] = {"status": self.status}
        irq_counts = {}
        for cpu, fd in self.irq_fds.items():
            try:
                irq_counts[str(cpu)] = read_perf_counter(fd)
            except OSError as exc:
                self.status["irq_entry"] = "final read failed: %s" % exc
            os.close(fd)
        self.irq_fds = {}
        out["irq_entry_counts"] = irq_counts
        if self.smi_method == "msr0x34":
            out["smi"] = {"method": "msr0x34", "start": self.smi_start, "end": read_msr_smi_count(self.cpus)}
        elif self.smi_method == "msr_pmu":
            end = {}
            for cpu, fd in self.smi_fds.items():
                try:
                    end[cpu] = read_perf_counter(fd)
                except OSError as exc:
                    self.status["smi"] = "final read failed: %s" % exc
                os.close(fd)
            self.smi_fds = {}
            out["smi"] = {"method": "msr_pmu", "start": self.smi_start, "end": end}
        else:
            out["smi"] = {"method": "unavailable"}
        return out


def perf_preflight(cpus: list) -> dict:
    """What perf/MSR observability this container allows (T6). Read-only
    probes: the perf binary's presence and version, the paranoid level, the
    PMU inventory, the msr event files, and whether perf_event_open accepts a
    per-process and a cpu-wide counting event. Every probe lands both as a
    convenience key and as a {source, available, error} record; failures are
    recorded, never raised. The cpu-wide tracepoint and msr/smi attempts are
    pure syscalls: without capabilities they fail with EACCES harmlessly.
    """
    probes: list[dict] = []

    def record(source: str, ok: bool, error: str | None) -> None:
        probes.append({"source": source, "available": ok, "error": error})

    pf: dict[str, object] = {"probes": probes}
    pf["perf"] = shutil.which("perf") or "missing"
    record("perf binary", pf["perf"] != "missing", None if pf["perf"] != "missing" else "not in PATH")
    if pf["perf"] != "missing":
        try:
            version = subprocess.run(["perf", "--version"], capture_output=True, text=True, timeout=10).stdout.strip()
            if version:
                pf["perf_version"] = version
        except (OSError, subprocess.SubprocessError) as exc:
            record("perf --version", False, str(exc))
    paranoid = _read(Path("/proc/sys/kernel/perf_event_paranoid"))
    pf["perf_event_paranoid"] = paranoid.strip() if paranoid is not None else None
    record(
        "/proc/sys/kernel/perf_event_paranoid",
        paranoid is not None,
        None if paranoid is not None else "unreadable",
    )
    pmu_list = sorted(d.name for d in _PMU_DEVICES.glob("*")) if _PMU_DEVICES.is_dir() else []
    pf["pmus"] = pmu_list
    if not pmu_list:
        record("/sys/bus/event_source/devices", False, "no PMU devices visible")
    msr_events = {}
    if (_PMU_DEVICES / "msr" / "events").is_dir():
        for f in sorted((_PMU_DEVICES / "msr" / "events").glob("*")):
            text = _read(f)
            if text is not None:
                msr_events[f.name] = text.strip()
    pf["msr_events"] = msr_events
    if not msr_events:
        record("/sys/bus/event_source/devices/msr/events", False, "no msr event files")
    try:
        os.close(perf_event_open(PerfEventAttr(PERF_TYPE_SOFTWARE, PERF_COUNT_SW_TASK_CLOCK), 0, -1))
        pf["probe_per_process"] = "ok"
        record("perf_event_open per-process task-clock", True, None)
    except OSError as exc:
        pf["probe_per_process"] = str(exc)
        record("perf_event_open per-process task-clock", False, str(exc))
    tp = tracepoint_id("irq/irq_handler_entry")
    if tp is None or not cpus:
        pf["probe_cpu_wide"] = "not probed (no tracepoint id or no allowed CPUs)"
        record(
            "tracepoint irq/irq_handler_entry id",
            False,
            "tracingfs not mounted or no allowed CPUs",
        )
    else:
        try:
            os.close(perf_event_open(PerfEventAttr(PERF_TYPE_TRACEPOINT, tp), -1, cpus[0]))
            pf["probe_cpu_wide"] = "ok"
            record("perf_event_open cpu-wide tracepoint", True, None)
        except OSError as exc:
            pf["probe_cpu_wide"] = str(exc)
            record("perf_event_open cpu-wide tracepoint", False, str(exc))
    return pf


def diagnose_proc_interrupts() -> dict:
    """Why /proc/interrupts reads back empty on the current hosts (it was
    readable in the first probe run): stat the file, show the mounts covering
    it, and try the same file through other /proc roots -- a bind-mount mask
    hides exactly one path, so the alternates tell masked-file from
    kernel-side emptiness. Read-only diagnosis: the mask is deliberate
    container hardening, so this run never mounts or umounts anything, it
    just records the evidence and reads the unmasked sources
    (/sys/kernel/irq/<N>/per_cpu_count, /proc/irq/<N>/effective_affinity_list)
    instead.
    """
    diag: dict[str, object] = {}
    try:
        st = os.stat("/proc/interrupts")
        diag["stat"] = {"size": st.st_size, "mode": oct(st.st_mode), "dev": st.st_dev, "ino": st.st_ino}
    except OSError as exc:
        diag["stat"] = str(exc)
    mounts = []
    text = _read(Path("/proc/self/mountinfo"))
    if text is not None:
        for line in text.splitlines():
            fields = line.split()
            # mountinfo: <id> <parent> <major:minor> <root> <mount point> ...
            if "interrupts" in (fields[4] if len(fields) > 4 else ""):
                mounts.append(line)
    diag["mountinfo"] = mounts
    alt: dict[str, object] = {}
    for path in ("/proc/self/root/proc/interrupts", "/proc/1/root/proc/interrupts"):
        try:
            with open(path, "rb") as fp:
                alt[path] = len(fp.read(64))
        except OSError as exc:
            alt[path] = str(exc)
    diag["alt_reads"] = alt
    return diag


def _descendant_pids(root_pid: int) -> list:
    """All pids (processes and threads) below root_pid in the /proc tree."""
    parents: dict[str, str] = {}
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        text = _read(Path("/proc") / entry / "stat")
        if text is None:
            continue
        try:
            parents[entry] = str(read_proc_pid_stat(text)[1])  # ppid
        except (IndexError, ValueError):
            continue
    desc = []
    changed = True
    # iterate to a fixed point: cheap and free of recursion limits
    known = {str(root_pid)}
    while changed:
        changed = False
        for pid, ppid in parents.items():
            if ppid in known and pid not in known:
                known.add(pid)
                desc.append(int(pid))
                changed = True
    # threads of each process
    threads = []
    for pid in [root_pid] + desc:
        for tid_dir in (Path("/proc") / str(pid) / "task").glob("*"):
            threads.append(int(tid_dir.name))
    return sorted(set(desc + threads) - {root_pid})


class CpuWatch:
    def __init__(self, out_dir: Path, cmd: list):
        self.out_dir = out_dir
        self.cmd = cmd
        try:
            # empty when the platform (or container) exposes no affinity API;
            # the watch then runs unpinned and records that in meta.json
            self.allowed = sorted(os.sched_getaffinity(0))
        except (AttributeError, OSError):
            self.allowed = []
        self.allowed_missing = not self.allowed
        self.stop = threading.Event()
        self.samples = 0
        self.start_time = 0.0
        self.end_time = 0.0
        self.errors: dict[str, int] = {}
        self.bench_cpus: list[int] = []
        self.pin_state: dict[str, object] = {}
        self.perf: PassivePerfCounters | None = None
        self._samples_file = None
        self._child = None
        self._lock = threading.Lock()

    def _err(self, source: str) -> None:
        with self._lock:
            self.errors[source] = self.errors.get(source, 0) + 1

    # -- pinning -----------------------------------------------------------

    def _maybe_pin(self) -> None:
        """Pin the watch off every CPU ever seen running a pinned benchmark.

        The harness tasksets each benchmark run to a strict subset of our
        allowed CPUs, so descendants whose affinity is a proper subset of the
        allowed set are the benchmark runs. Discovery is one-sided early (the
        two sides' venv installs finish at different times), so the exclusion
        set only grows: each tick re-pins the watch to allowed CPUs minus
        everything seen so far, tightening as the other half appears.
        """
        if self.allowed_missing:
            self.pin_state = {
                "pinned": False,
                "reason": "no allowed-CPU set available",
                "bench_cpus": self.bench_cpus,
            }
            return
        try:
            bench = set(self.bench_cpus)
            # the root child is included: a wrapper may exec the pinned
            # command in place (taskset execs the benchmark), in which case
            # there is no intermediate descendant to observe
            for pid in [self._child.pid] + _descendant_pids(self._child.pid):
                text = _read(Path("/proc") / str(pid) / "status")
                if text is None:
                    continue
                for line in text.splitlines():
                    if line.startswith("Cpus_allowed_list:"):
                        cpus = set(parse_cpulist(line.split(":", 1)[1]))
                        if cpus and cpus != set(self.allowed):
                            bench |= cpus
                        break
            if bench == set(self.bench_cpus) and self.pin_state.get("pinned"):
                return  # nothing new; already pinned off everything seen
            self.bench_cpus = sorted(bench)
            pin = sorted(set(self.allowed) - bench)
            if not pin:
                self.pin_state = {
                    "pinned": False,
                    "reason": "allowed CPUs are all benchmark CPUs",
                    "bench_cpus": self.bench_cpus,
                }
                return
            # pin every thread of the watch process
            for tid_dir in Path("/proc/self/task").glob("*"):
                try:
                    os.sched_setaffinity(int(tid_dir.name), pin)
                except OSError:
                    pass
            self.pin_state = {"pinned": True, "watch_cpus": pin, "bench_cpus": self.bench_cpus}
        except OSError:
            pass

    # -- samples -----------------------------------------------------------

    def _collect(self) -> dict:
        sample: dict[str, object] = {"t": time.time()}

        text = _read(Path("/proc/stat"))
        if text is not None:
            sample["stat"] = read_stat(text)
        else:
            self._err("proc_stat")

        text = _read(Path("/proc/interrupts"))
        if text is not None:
            sample["interrupts"] = read_interrupts(text)
        else:
            self._err("proc_interrupts")

        # /sys/kernel/irq per-CPU counts at 1 Hz: the IRQ/s-by-device-and-CPU
        # timeline the H1 readout needs when /proc/interrupts is masked
        irq_counts = {}
        for irq_dir in sorted(Path("/sys/kernel/irq").glob("[0-9]*")):
            text = _read(irq_dir / "per_cpu_count")
            if text is not None:
                try:
                    irq_counts[irq_dir.name] = read_irq_per_cpu_count(text)
                except ValueError:
                    self._err("sys_kernel_irq")
        if irq_counts:
            sample["irq_per_cpu_counts"] = irq_counts
        else:
            self._err("sys_kernel_irq")

        text = _read(Path("/proc/schedstat"))
        if text is not None:
            if text.strip():
                sample["schedstat"] = read_schedstat(text)
            else:
                self._err("proc_schedstat")
        else:
            self._err("proc_schedstat")

        if self.perf is not None:
            counts = self.perf.sample()
            if counts:
                sample["irq_entry_counts"] = counts

        text = _read(Path("/proc/softirqs"))
        if text is not None:
            sample["softirqs"] = read_softirqs(text)
        else:
            self._err("proc_softirqs")

        psi = {}
        for res in ("cpu", "memory", "io"):
            text = _read(Path("/proc/pressure") / res)
            if text is not None:
                psi[res] = read_psi(text)
            else:
                self._err("psi_" + res)
        sample["psi"] = psi

        text = _read(Path("/proc/vmstat"))
        if text is not None:
            sample["vmstat"] = read_vmstat(text)
        else:
            self._err("proc_vmstat")

        numastat = {}
        for node_dir in sorted(_NODE_SYS.glob("node*")):
            text = _read(node_dir / "numastat")
            if text is not None:
                numastat[node_dir.name] = read_numastat(text)
            else:
                self._err("numastat")
        sample["numastat"] = numastat

        throttle = {}
        for cpu_dir in sorted(_CPU_SYS.glob("cpu[0-9]*")):
            cpu = cpu_dir.name[3:]
            entry = {}
            for name in ("package_throttle_count", "core_throttle_count"):
                value = _read(cpu_dir / "thermal_throttle" / name)
                if value is not None:
                    entry[name] = int(value.strip() or 0)
            if entry:
                throttle[cpu] = entry
        sample["thermal_throttle"] = throttle

        freq = {}
        for cpu_dir in sorted(_CPU_SYS.glob("cpu[0-9]*")):
            value = _read(cpu_dir / "cpufreq" / "scaling_cur_freq")
            if value is not None:
                freq[cpu_dir.name[3:]] = int(value.strip())
        sample["freq"] = freq

        # scaling_cur_freq is masked in the benchmark containers; /proc/cpuinfo
        # MHz is the readable equivalent for the per-cpu frequency timeline
        cpuinfo = _read(Path("/proc/cpuinfo"))
        if cpuinfo is not None:
            sample["mhz"] = read_cpuinfo_mhz(cpuinfo)
        else:
            self._err("cpuinfo_mhz")

        try:
            proc_sample = sample_procs()
            sample["procs"] = proc_sample["procs"]
            if proc_sample["sched"]:
                sample["pid_sched"] = proc_sample["sched"]
            if proc_sample["ctxt"]:
                sample["pid_ctxt"] = proc_sample["ctxt"]
        except OSError:
            self._err("procs")

        return sample

    def _sampler_loop(self) -> None:
        # schedule on the monotonic clock so drift never compounds past 1 Hz
        next_t = time.monotonic()
        ticks_since_child = 0
        while not self.stop.is_set():
            if self._child is not None and ticks_since_child < PIN_DISCOVERY_TICKS:
                self._maybe_pin()
                ticks_since_child += 1
            sample = self._collect()
            try:
                self._samples_file.write((json.dumps(sample, separators=(",", ":")) + "\n").encode("utf-8"))
                # flush the zlib buffer each sample: if the job is killed by a
                # CI timeout, everything sampled so far is still readable
                self._samples_file.flush()
                with self._lock:
                    self.samples += 1
            except OSError:
                self._err("samples_write")
                return
            next_t += SAMPLE_INTERVAL_S
            delay = next_t - time.monotonic()
            if delay > 0:
                self.stop.wait(delay)

    # -- static snapshot ---------------------------------------------------

    def _static_snapshot(self) -> dict:
        snap: dict[str, object] = {"watch_pid": os.getpid(), "allowed_cpus": self.allowed}

        online = _read(_CPU_SYS / "online")
        snap["cpu_online"] = online.strip() if online is not None else None

        topo = {}
        for cpu_dir in sorted(_CPU_SYS.glob("cpu[0-9]*")):
            cpu = cpu_dir.name[3:]
            entry = {}
            for name in ("core_id", "physical_package_id", "thread_siblings_list"):
                value = _read(cpu_dir / "topology" / name)
                if value is not None:
                    entry[name] = value.strip()
            if entry:
                topo[cpu] = entry
        snap["topology"] = topo

        nodes = {}
        for node_dir in sorted(_NODE_SYS.glob("node*")):
            cpulist = _read(node_dir / "cpulist")
            if cpulist is not None:
                nodes[node_dir.name] = cpulist.strip()
        snap["numa"] = nodes

        snap["uname"] = list(os.uname())
        cmdline = _read(Path("/proc/cmdline"))
        snap["kernel_cmdline"] = cmdline.strip() if cmdline is not None else None
        version = _read(Path("/proc/version"))
        snap["kernel_version"] = version.strip() if version is not None else None

        cpuinfo = _read(Path("/proc/cpuinfo"))
        snap["cpuinfo"] = read_cpuinfo(cpuinfo) if cpuinfo is not None else None

        freq_policy = {}
        for cpu_dir in sorted(_CPU_SYS.glob("cpu[0-9]*")):
            entry = {}
            for name in ("scaling_driver", "scaling_governor", "cpuinfo_min_freq", "cpuinfo_max_freq"):
                value = _read(cpu_dir / "cpufreq" / name)
                if value is not None:
                    entry[name] = value.strip()
            if entry:
                freq_policy[cpu_dir.name[3:]] = entry
        snap["cpufreq_policy"] = freq_policy

        irq_affinity = {}
        devices = {}
        interrupts = _read(Path("/proc/interrupts")) or ""
        interrupts_report: dict[str, object] = {
            "diag": diagnose_proc_interrupts(),
            "bytes": len(interrupts),
        }
        snap["proc_interrupts_report"] = interrupts_report
        if interrupts.strip():
            parsed = read_interrupts(interrupts)
            devices = {key: row["dev"] for key, row in parsed.get("rows", {}).items()}
        for irq_dir in sorted(Path("/proc/irq").glob("[0-9]*")):
            aff = _read(irq_dir / "smp_affinity_list")
            eff = _read(irq_dir / "effective_affinity_list")
            actions = _read(irq_dir / "actions")
            if aff is None and eff is None:
                continue
            # allowed (smp_affinity_list) vs where the kernel actually routes
            # each line (effective_affinity_list) vs its device names (actions,
            # /proc/interrupts's trailing column when readable) -- the join the
            # H1 readout needs on the 20 lines allowed near CPU 24 (F12)
            irq_affinity[irq_dir.name] = {
                "affinity": aff.strip() if aff is not None else None,
                "effective": eff.strip() if eff is not None else None,
                "actions": actions.strip() if actions is not None else None,
                "dev": devices.get(irq_dir.name, ""),
            }
        snap["irq_affinity"] = irq_affinity

        # /sys/kernel/irq/<N>/per_cpu_count: per-CPU cumulative counts per IRQ
        # line, readable even where /proc/interrupts is masked. Sampled again
        # every second, it gives hardware IRQs/s by device and CPU (H1).
        kernel_irq = {}
        for irq_dir in sorted(Path("/sys/kernel/irq").glob("[0-9]*")):
            counts = _read(irq_dir / "per_cpu_count")
            if counts is None:
                continue
            try:
                kernel_irq[irq_dir.name] = {
                    "counts": read_irq_per_cpu_count(counts),
                    "actions": (_read(irq_dir / "actions") or "").strip(),
                    "effective": (_read(irq_dir / "effective_affinity_list") or "").strip(),
                }
            except (OSError, ValueError):
                continue
        snap["irq_per_cpu_counts"] = kernel_irq

        cgroup = _read(Path("/proc/self/cgroup"))
        snap["cgroup"] = cgroup.strip() if cgroup is not None else None
        cgroup_cpuset = {}
        for name in ("cpuset.cpus", "cpuset.cpus.effective", "cpuset.mems.effective"):
            value = _read(Path("/sys/fs/cgroup") / name)
            if value is not None:
                cgroup_cpuset[name] = value.strip()
        snap["cgroup_cpuset"] = cgroup_cpuset

        # Which counter sources were readable at startup; the completeness
        # check reports missing ones instead of guessing. A source that reads
        # back empty (the containers mask /proc/interrupts entirely) counts as
        # NOT readable.
        sources = {
            "proc_stat": _read(Path("/proc/stat")) is not None,
            "proc_interrupts": bool(interrupts.strip()),
            "proc_softirqs": bool((_read(Path("/proc/softirqs")) or "").strip()),
            "psi_cpu": _read(Path("/proc/pressure/cpu")) is not None,
            "psi_memory": _read(Path("/proc/pressure/memory")) is not None,
            "psi_io": _read(Path("/proc/pressure/io")) is not None,
            "proc_vmstat": _read(Path("/proc/vmstat")) is not None,
            "numastat": _read(next(iter(_NODE_SYS.glob("node*")), _NODE_SYS / "node0") / "numastat") is not None,
            "thermal_throttle": any(
                _read(d / "thermal_throttle" / "core_throttle_count") is not None
                for d in list(_CPU_SYS.glob("cpu[0-9]*"))[:1]
            ),
            "freq": any(
                _read(d / "cpufreq" / "scaling_cur_freq") is not None for d in list(_CPU_SYS.glob("cpu[0-9]*"))[:1]
            ),
            "proc_schedstat": bool((_read(Path("/proc/schedstat")) or "").strip()),
            "pid_schedstat": bool((_read(Path("/proc/self/schedstat")) or "").strip()),
            "proc_pid_stat": _read(Path("/proc/self/stat")) is not None,
        }
        snap["sources_readable"] = sources
        snap["perf_preflight"] = perf_preflight(self.allowed)
        snap["source_probes"] = self._source_probes()
        return snap

    def _source_probes(self) -> list:
        """One best-effort {source, available, error} record per counter
        source the run depends on (T6): every read this watch makes is
        accounted for, with the error string when it failed. Never raises.
        """
        probes = [
            source_probe(Path("/proc/stat")),
            source_probe(Path("/proc/interrupts")),
            source_probe(Path("/proc/softirqs")),
            source_probe(Path("/proc/schedstat")),
            source_probe(Path("/proc/self/schedstat")),
            source_probe(Path("/proc/self/status")),
            source_probe(Path("/proc/self/stat")),
            source_probe(Path("/proc/pressure/cpu")),
            source_probe(Path("/proc/vmstat")),
            source_probe(next(iter(_NODE_SYS.glob("node*")), _NODE_SYS / "node0") / "numastat"),
            source_probe(
                next(iter(_CPU_SYS.glob("cpu[0-9]*")), _CPU_SYS / "cpu0") / "thermal_throttle" / "core_throttle_count"
            ),
            source_probe(next(iter(_CPU_SYS.glob("cpu[0-9]*")), _CPU_SYS / "cpu0") / "cpufreq" / "scaling_cur_freq"),
        ]
        for base in (Path("/proc/irq"), Path("/sys/kernel/irq")):
            first = next(iter(sorted(base.glob("[0-9]*"))), None)
            if first is None:
                probes.append({"source": str(base), "available": False, "error": "no irq directories"})
            else:
                probes.append(source_probe(first / "effective_affinity_list"))
                probes.append(source_probe(first / "actions"))
                if base == Path("/sys/kernel/irq"):
                    probes.append(source_probe(first / "per_cpu_count"))
        if self.allowed:
            probes.append(source_probe(Path("/dev/cpu/%d/msr" % self.allowed[0])))
        return probes

    # -- main --------------------------------------------------------------

    def _irq_effective_now(self) -> dict:
        """/proc/irq/*/effective_affinity_list re-read at end: irqbalance on the
        host can move lines between the static snapshot and the end of the
        job, which is exactly the T6 question (where the 20 lines allowed near
        CPU 24 actually land).
        """
        eff = {}
        for irq_dir in sorted(Path("/proc/irq").glob("[0-9]*")):
            value = _read(irq_dir / "effective_affinity_list")
            if value is not None:
                eff[irq_dir.name] = value.strip()
        return eff

    def _end_snapshot(self) -> dict:
        end: dict[str, object] = {"t": time.time(), "irq_effective": self._irq_effective_now()}
        if self.perf is not None:
            end["perf"] = self.perf.finish()
        # the same best-effort accounting for the end-of-run re-reads (T6):
        # whatever failed to re-read is recorded with its error, never raised
        end["source_probes"] = [
            source_probe(
                next(iter(sorted(Path("/proc/irq").glob("[0-9]*"))), Path("/proc/irq/0")) / "effective_affinity_list"
            ),
            source_probe(
                next(iter(sorted(Path("/sys/kernel/irq").glob("[0-9]*"))), Path("/sys/kernel/irq/0")) / "per_cpu_count"
            ),
        ]
        if self.allowed:
            end["source_probes"].append(source_probe(Path("/dev/cpu/%d/msr" % self.allowed[0])))
        return end

    def run(self) -> int:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        static = self._static_snapshot()
        (self.out_dir / "static.json").write_text(json.dumps(static, indent=1) + "\n")
        # per-CPU irq:irq_handler_entry and msr/smi counters for the whole run;
        # failures are recorded inside, never raised (T6)
        self.perf = PassivePerfCounters(self.allowed)

        self.start_time = time.time()
        try:
            self._child = subprocess.Popen(self.cmd)
        except OSError as exc:
            # meta.json still lands in the artifacts so the completeness
            # check explains the missing samples instead of guessing
            meta = {
                "child_pid": None,
                "child_rc": 127,
                "start": self.start_time,
                "end": time.time(),
                "samples": 0,
                "interval_s": SAMPLE_INTERVAL_S,
                "allowed_cpus": self.allowed,
                "pin": self.pin_state or {"pinned": False, "reason": "child never spawned"},
                "self_cpu_time_s": time.process_time(),
                "errors": {"spawn": str(exc)},
            }
            (self.out_dir / "meta.json").write_text(json.dumps(meta, indent=1) + "\n")
            return 127
        self._samples_file = gzip.open(self.out_dir / "samples.jsonl.gz", "wb")
        sampler = threading.Thread(target=self._sampler_loop, daemon=True)
        sampler.start()
        try:
            rc = self._child.wait()
        finally:
            # one sample after the child exits, then stop
            self.stop.set()
            sampler.join(timeout=5 * SAMPLE_INTERVAL_S)
            self.end_time = time.time()
            try:
                (self.out_dir / "end.json").write_text(json.dumps(self._end_snapshot(), indent=1) + "\n")
            except OSError:
                pass  # meta.json below still explains the run
            try:
                self._samples_file.close()
            except OSError:
                pass
        if not self.pin_state:
            self.pin_state = {"pinned": False, "reason": "no pinned descendants found"}
        meta = {
            "child_pid": self._child.pid,
            "child_rc": rc,
            "start": self.start_time,
            "end": self.end_time,
            "samples": self.samples,
            "interval_s": SAMPLE_INTERVAL_S,
            "allowed_cpus": self.allowed,
            "pin": self.pin_state,
            "self_cpu_time_s": time.process_time(),
            "errors": self.errors,
        }
        (self.out_dir / "meta.json").write_text(json.dumps(meta, indent=1) + "\n")
        return rc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True, help="output directory")
    parser.add_argument("cmd", nargs=argparse.REMAINDER, help="command after --")
    args = parser.parse_args()
    cmd = args.cmd
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        parser.error("no command to watch (use: watch.py --out DIR -- CMD)")
    return CpuWatch(Path(args.out), cmd).run()


if __name__ == "__main__":
    sys.exit(main())
