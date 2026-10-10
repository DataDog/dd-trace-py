#!/usr/bin/env python3
"""Preflight: what perf/MSR/IRQ observability this container allows.

EXPERIMENT (do not merge): T6 of the CPU-asymmetry probe (PR #20052 /
APMSP-4059). Runs in the CI job strictly before the benchmarks, records every
probe as a best-effort {source, available, error} entry, and never raises and
never fails the job: the caller invokes it with `|| true`-equivalent guards.

Output: <out>/preflight.json (records + raw values) and <out>/preflight.txt
(human-readable rendering). Stdlib only; reads /proc and /sys the same way
the T5 watch already did in these containers (which is why this is Python
and not shell: the previous bash preflight died silently in the job
container, while Python reads of the same paths are proven safe).
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


# user-only by default: the CI containers run unprivileged under
# perf_event_paranoid=2, which forbids kernel-inclusive counts, and the
# benchmark workload itself is user space
PERF_STAT_TRIAL_EVENTS = (
    "task-clock,cycles:u,instructions:u,cache-references:u,cache-misses:u,context-switches,cpu-migrations,page-faults"
)


def read_source(path) -> dict:
    """Best-effort read of one source: {source, available, error, bytes,
    text}. Never raises; empty content counts as available-but-empty (that is
    how /proc/interrupts is masked in these containers) and is reported
    separately from read failures.
    """
    record: dict = {"source": str(path), "available": False, "error": None, "bytes": None, "text": None}
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        record["error"] = "%s: %s" % (type(exc).__name__, exc)
        return record
    record["available"] = True
    record["bytes"] = len(data)
    record["text"] = data[:400].decode("utf-8", "replace")
    return record


def run_probe(label: str, args: list) -> dict:
    """Best-effort subprocess probe: {source, available, rc, stderr}. The
    perf stat trials are pure attempts; without capabilities they fail with
    EACCES and that is recorded.
    """
    record: dict = {"source": label, "available": False, "error": None, "rc": None, "stderr": None}
    try:
        proc = subprocess.run(args, capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        record["error"] = "%s: %s" % (type(exc).__name__, exc)
        return record
    record["rc"] = proc.returncode
    record["stderr"] = proc.stderr.decode("utf-8", "replace")[:400]
    record["available"] = proc.returncode == 0
    if proc.returncode != 0 and not record["stderr"]:
        record["error"] = "rc=%d (no stderr)" % proc.returncode
    return record


def list_dir(path) -> dict:
    """Best-effort directory listing: {source, available, error, entries}."""
    record: dict = {"source": str(path), "available": False, "error": None, "entries": None}
    try:
        record["entries"] = sorted(os.listdir(path))
    except OSError as exc:
        record["error"] = "%s: %s" % (type(exc).__name__, exc)
        return record
    record["available"] = True
    return record


def cap_eff() -> str | None:
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("CapEff:"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return None


def probe(cpus: list) -> dict:
    out: dict = {"t": time.time(), "uid": None, "caps": cap_eff(), "cpus": cpus, "records": []}
    try:
        out["uid"] = os.getuid()
    except (AttributeError, OSError):
        pass

    def add(record: dict) -> None:
        out["records"].append(record)

    # identity and perf tooling
    perf_path = shutil.which("perf")
    out["perf"] = perf_path or "missing"
    add(
        {
            "source": "perf binary",
            "available": perf_path is not None,
            "error": None if perf_path else "not in PATH",
        }
    )
    if perf_path:
        add(run_probe("perf --version", ["perf", "--version"]))
        add(run_probe("perf stat user-only trial", ["perf", "stat", "-x,", "-e", PERF_STAT_TRIAL_EVENTS, "--", "true"]))
        for cpu in cpus[:1]:
            add(
                run_probe(
                    "perf stat cpu-wide msr/smi trial",
                    ["perf", "stat", "-x,", "-e", "msr/smi/", "-C", str(cpu), "--", "true"],
                )
            )

    # kernel perf policy and PMU inventory
    add(read_source("/proc/sys/kernel/perf_event_paranoid"))
    add(list_dir("/sys/bus/event_source/devices"))
    add(list_dir("/sys/bus/event_source/devices/msr/events"))
    add(list_dir("/dev/cpu"))

    # IRQ observability: the masked /proc/interrupts plus the unmasked
    # stand-ins the T6 readout actually uses
    add(read_source("/proc/interrupts"))
    add(read_source("/proc/schedstat"))
    add(read_source("/proc/self/schedstat"))
    add(list_dir("/sys/kernel/irq"))
    add(list_dir("/proc/irq"))
    for cpu in cpus[:1]:
        for path in (
            "/sys/kernel/irq/%d/per_cpu_count" % cpu,
            "/sys/kernel/irq/%d/actions" % cpu,
            "/sys/kernel/irq/%d/effective_affinity_list" % cpu,
            "/proc/irq/%d/effective_affinity_list" % cpu,
            "/proc/irq/%d/actions" % cpu,
        ):
            add(read_source(path))
    return out


def render(report: dict) -> str:
    lines = ["preflight %s uid=%s caps=%s perf=%s" % (report["t"], report["uid"], report["caps"], report["perf"])]
    for record in report["records"]:
        if "bytes" in record and record["bytes"] is not None:
            lines.append(
                "%s: available=%s bytes=%s error=%s text=%r"
                % (
                    record["source"],
                    record["available"],
                    record["bytes"],
                    record["error"],
                    (record["text"] or "")[:120],
                )
            )
        elif "entries" in record and record["entries"] is not None:
            lines.append(
                "%s: available=%s n=%d error=%s sample=%s"
                % (
                    record["source"],
                    record["available"],
                    len(record["entries"]),
                    record["error"],
                    record["entries"][:12],
                )
            )
        elif "rc" in record and record["rc"] is not None:
            lines.append(
                "%s: available=%s rc=%s error=%s stderr=%s"
                % (record["source"], record["available"], record["rc"], record["error"], (record["stderr"] or "")[:200])
            )
        else:
            lines.append("%s: available=%s error=%s" % (record["source"], record["available"], record["error"]))
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True, help="output directory")
    parser.add_argument("--cpus", default="24,25,36,37", help="comma-separated probe CPUs")
    args = parser.parse_args()
    cpus = [int(cpu) for cpu in args.cpus.split(",") if cpu.strip()]
    try:
        report = probe(cpus)
    except Exception as exc:  # a preflight failure must never fail the job
        report = {"t": time.time(), "records": [], "error": "probe() raised: %s: %s" % (type(exc).__name__, exc)}
    out_dir = Path(args.out)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "preflight.json").write_text(json.dumps(report, indent=1) + "\n")
        (out_dir / "preflight.txt").write_text(render(report))
    except OSError as exc:
        print("preflight: could not write %s: %s" % (out_dir, exc), file=sys.stderr)
    print(render(report), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
