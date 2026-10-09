#!/usr/bin/env python3
"""CPU watch: passive per-core/device counter sampler around a benchmark command.

EXPERIMENT (do not merge): CPU-asymmetry probe on benchmarking hosts
(PR #20052 / APMSP-4059). Runs alongside the microbenchmark harness and
records, at 1 Hz, what every CPU and device is doing, so each config's
candidate/baseline delta can be joined with the counters of the cores and
time window it ran on. See the spec card and lab notebook in the vault.

Usage: watch.py --out DIR -- CMD [ARGS...]

Output (under DIR):
  static.json      one-time snapshot: topology, NUMA, IRQ affinities, cpufreq
                   policy, kernel, cgroup cpuset, allowed CPUs, source readability
  samples.jsonl.gz 1 Hz samples: per-CPU /proc/stat, /proc/interrupts,
                   /proc/softirqs, PSI, selected vmstat, numastat, thermal
                   throttle counts, per-CPU frequency, per-process stat
  meta.json        run summary: pinning decision, own CPU time, read errors

Self-intrusion controls:
  - Pins itself (all threads) to allowed CPUs outside the benchmark ranges,
    discovered from the affinity of taskset-pinned descendants; records the
    decision when pinning isn't possible.
  - Records its own CPU time; per-process samples include the watch itself.
  - Never touches MSRs or perf/PMU, and generates no load beyond reading
    /proc and /sys at 1 Hz.

Stdlib only; the samples file is gzip JSONL, one JSON object per line with an
"t" epoch-seconds key that joins with placement.jsonl timestamps from the
harness's run.py.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
from pathlib import Path
import re
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


def _read(path: Path):
    try:
        return path.read_text()
    except OSError:
        return None


def sample_procs() -> dict:
    """Per-process stat for every /proc-visible pid: comm, ppid, utime, stime,
    last CPU. Lets the readout split a core's busy time into "ours" vs
    "someone else's" (H6).
    """
    procs: dict[str, list] = {}
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        text = _read(Path("/proc") / entry / "stat")
        if text is None:
            continue
        try:
            procs[entry] = read_proc_pid_stat(text)
        except (IndexError, ValueError):
            continue
    return procs


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

        try:
            sample["procs"] = sample_procs()
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
        interrupts = _read(Path("/proc/interrupts"))
        devices = {}
        if interrupts is not None:
            parsed = read_interrupts(interrupts)
            devices = {key: row["dev"] for key, row in parsed.get("rows", {}).items()}
        for irq_dir in sorted(Path("/proc/irq").glob("[0-9]*")):
            aff = _read(irq_dir / "smp_affinity_list")
            if aff is not None:
                irq_affinity[irq_dir.name] = {
                    "affinity": aff.strip(),
                    "dev": devices.get(irq_dir.name, ""),
                }
        snap["irq_affinity"] = irq_affinity

        cgroup = _read(Path("/proc/self/cgroup"))
        snap["cgroup"] = cgroup.strip() if cgroup is not None else None
        cgroup_cpuset = {}
        for name in ("cpuset.cpus", "cpuset.cpus.effective", "cpuset.mems.effective"):
            value = _read(Path("/sys/fs/cgroup") / name)
            if value is not None:
                cgroup_cpuset[name] = value.strip()
        snap["cgroup_cpuset"] = cgroup_cpuset

        # Which counter sources were readable at startup; the completeness
        # check reports missing ones instead of guessing.
        sources = {
            "proc_stat": _read(Path("/proc/stat")) is not None,
            "proc_interrupts": interrupts is not None,
            "proc_softirqs": _read(Path("/proc/softirqs")) is not None,
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
            "proc_pid_stat": _read(Path("/proc/self/stat")) is not None,
        }
        snap["sources_readable"] = sources
        return snap

    # -- main --------------------------------------------------------------

    def run(self) -> int:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        static = self._static_snapshot()
        (self.out_dir / "static.json").write_text(json.dumps(static, indent=1) + "\n")

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
