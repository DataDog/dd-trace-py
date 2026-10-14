#!/usr/bin/env python3
"""Unit tests for the CPU watch parsers and the placement-record join key.

EXPERIMENT (do not merge): CPU-asymmetry probe (PR #20052 / APMSP-4059).
This is a CI step script, not ddtrace product code, so it is tested here with
stdlib unittest only -- no pytest, no riot, no test venv. The /proc excerpts
below pin the parser shapes against real kernel output so counter formats are
guaranteed before a CI run consumes them.

Run from the repo root (finishes in well under a second):
    python3 .gitlab/benchmarks/steps/test_watch.py
    python3 -m unittest discover -s .gitlab/benchmarks/steps -p "test_watch.py" -v
"""

import gzip
import importlib.util
import json
import os
from pathlib import Path
import sys
import types
import unittest


_HERE = Path(__file__).resolve().parent


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_run_py():
    # run.py imports yaml only for its own config loading; the placement
    # record does not use it, so a stub keeps this test dependency-free
    saved = sys.modules.get("yaml")
    sys.modules["yaml"] = types.ModuleType("yaml")
    try:
        return _load("benchmarks_base_run", _HERE.parents[2] / "benchmarks" / "base" / "run.py")
    finally:
        if saved is not None:
            sys.modules["yaml"] = saved
        else:
            del sys.modules["yaml"]


WATCH = _load("watch", _HERE / "watch.py")

PROC_STAT = """\
cpu  100 0 200 5000 10 5 3 2 0 0
cpu0 10 0 20 500 1 0 1 0 0 0
cpu1 11 0 21 501 1 1 0 1 0 0
cpu2 12 0 22 502 1 2 0 0 0 0
intr 12345 1 2 3
ctxt 6789
"""

PROC_INTERRUPTS = """\
           CPU0       CPU1       CPU2
  0:        10         0         0  IO-APIC   2-edge    timer
  1:         0        20         0  IO-APIC   1-edge    i8042
 27:      1000      2000      3000  PCI-MSI 512000-edge  eth0-rx-0
NMI:         2         1         0  Non-maskable interrupts
CAL:         3         0         0  Function call interrupts
ERR:         0
"""

PROC_SOFTIRQS = """\
                    CPU0       CPU1       CPU2
          HI:          1          2          3
       TIMER:         10         20         30
      NET_RX:          0          5          0
"""

PROC_CPUINFO = """\
processor\t: 24
model name\t: Intel(R) Xeon(R) Platinum 8175M CPU @ 2.50GHz
cpu MHz\t\t: 2100.000
microcode\t: 0x500002c

processor\t: 25
model name\t: Intel(R) Xeon(R) Platinum 8175M CPU @ 2.50GHz
cpu MHz\t\t: 2499.938
microcode\t: 0x500002c
"""


class TestParsers(unittest.TestCase):
    def test_read_stat_per_cpu_fields(self):
        parsed = WATCH.read_stat(PROC_STAT)
        self.assertEqual(parsed["cpu0"][0], 10)  # user
        self.assertEqual(parsed["cpu1"][5], 1)  # irq
        self.assertEqual(parsed["cpu1"][6], 0)  # softirq
        self.assertEqual(parsed["cpu2"][7], 0)  # steal
        self.assertEqual(parsed["cpu"][0], 100)  # aggregate row
        # "intr" is a total followed by per-IRQ counts; keep the total only
        self.assertEqual(parsed["intr"], 12345)
        self.assertEqual(parsed["ctxt"], 6789)

    def test_read_interrupts_rows_and_devices(self):
        parsed = WATCH.read_interrupts(PROC_INTERRUPTS)
        self.assertEqual(parsed["cpus"], ["CPU0", "CPU1", "CPU2"])
        self.assertEqual(parsed["rows"]["27"]["dev"], "PCI-MSI 512000-edge eth0-rx-0")
        self.assertEqual(parsed["rows"]["27"]["counts"], [1000, 2000, 3000])
        # IPI rows (CAL) carry the per-CPU counts the IPI check needs
        self.assertEqual(parsed["rows"]["CAL"]["counts"], [3, 0, 0])
        # a row with only a grand total and no per-CPU counts is skipped
        self.assertNotIn("ERR", parsed["rows"])

    def test_read_softirqs_rows(self):
        parsed = WATCH.read_softirqs(PROC_SOFTIRQS)
        self.assertEqual(parsed["HI"], [1, 2, 3])
        self.assertEqual(parsed["TIMER"], [10, 20, 30])
        self.assertEqual(parsed["NET_RX"], [0, 5, 0])

    def test_read_psi(self):
        parsed = WATCH.read_psi("some avg10=0.00 avg60=0.01 avg300=0.00 total=12345\nfull avg10=1.0 total=678\n")
        self.assertEqual(parsed["some"]["avg60"], 0.01)
        self.assertEqual(parsed["some"]["total"], 12345)
        self.assertEqual(parsed["full"]["total"], 678)

    def test_read_vmstat_filters_by_prefix(self):
        parsed = WATCH.read_vmstat(
            "pgfault 100\npgmajfault 2\nnuma_hit 1000\nnuma_miss 7\nnuma_foreign 0\n"
            "unrelated_key 42\npswpin 0\nworkingset_refault 5\n"
        )
        self.assertEqual(
            parsed,
            {
                "pgfault": 100,
                "pgmajfault": 2,
                "numa_hit": 1000,
                "numa_miss": 7,
                "numa_foreign": 0,
                "pswpin": 0,
                "workingset_refault": 5,
            },
        )

    def test_read_numastat_layouts(self):
        # new kernels: one pair per line
        self.assertEqual(
            WATCH.read_numastat("numa_hit 100\nnuma_miss 0\nnuma_foreign 0\n"),
            {"numa_hit": 100, "numa_miss": 0, "numa_foreign": 0},
        )
        # old kernels: aligned columns, several pairs per line
        self.assertEqual(
            WATCH.read_numastat("numa_hit                 100     numa_miss                0\n"),
            {"numa_hit": 100, "numa_miss": 0},
        )

    def test_read_cpuinfo_and_mhz(self):
        parsed = WATCH.read_cpuinfo(PROC_CPUINFO)
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0]["model name"], "Intel(R) Xeon(R) Platinum 8175M CPU @ 2.50GHz")
        self.assertEqual(parsed[1]["microcode"], "0x500002c")
        mhz = WATCH.read_cpuinfo_mhz(PROC_CPUINFO)
        self.assertEqual(mhz, {"24": 2100.0, "25": 2499.938})
        # unreadable values are skipped, not guessed
        self.assertEqual(WATCH.read_cpuinfo_mhz("processor\t: 0\ncpu MHz\t\t: not-a-number\n"), {})

    def test_parse_cpulist(self):
        self.assertEqual(WATCH.parse_cpulist("24-35"), list(range(24, 36)))
        self.assertEqual(WATCH.parse_cpulist("24-35,40"), list(range(24, 36)) + [40])
        self.assertEqual(WATCH.parse_cpulist("0"), [0])
        self.assertEqual(WATCH.parse_cpulist(""), [])


class TestPidStat(unittest.TestCase):
    @staticmethod
    def _pid_stat_line(pid, comm, state="R", ppid=1, utime=100, stime=200, processor=39):
        # stat(5) layout: field 3 (state) lands at index 0 of the post-')'
        # split, fields 14/15 (utime/stime) at 11/12, field 39 at 36
        fields = [str(i) for i in range(3, 39)]
        fields[0] = state
        fields[1] = str(ppid)
        fields[11] = str(utime)
        fields[12] = str(stime)
        fields.append(str(processor))
        return "%d (%s) %s\n" % (pid, comm, " ".join(fields))

    def test_read_proc_pid_stat(self):
        self.assertEqual(
            WATCH.read_proc_pid_stat(self._pid_stat_line(42, "python3")),
            ["python3", 1, 100, 200, 39],
        )

    def test_read_proc_pid_stat_comm_with_parens_and_spaces(self):
        line = self._pid_stat_line(7, "task (worker)", state="S", ppid=40, utime=11, stime=22, processor=5)
        self.assertEqual(WATCH.read_proc_pid_stat(line), ["task (worker)", 40, 11, 22, 5])


class TestJoinContract(unittest.TestCase):
    def test_placement_record_roundtrip(self):
        # the placement record is the join key between results.json (config
        # names) and the watch samples (t, per-CPU counters); keep the
        # shapes in sync with the readout tooling
        run_mod = _load_run_py()
        out = _HERE / "test_placement_out"
        out.mkdir(exist_ok=True)
        try:
            (out / "candidate").mkdir(exist_ok=True)
            run_mod.append_placement_record(str(out / "candidate"), "span-start", [24], 100.0, 105.0, 4242)
            run_mod.append_placement_record(str(out / "candidate"), "span-start-finish", [25], 106.0, 111.0, 4243)
            records = [json.loads(ln) for ln in (out / "candidate" / "placement.jsonl").read_text().splitlines()]
            self.assertEqual(
                records[0],
                {
                    "scenario": None,
                    "side": "candidate",
                    "config": "span-start",
                    "cpus": [24],
                    "start": 100.0,
                    "end": 105.0,
                    "pid": 4242,
                },
            )
            self.assertEqual(records[1]["config"], "span-start-finish")
        finally:
            import shutil

            shutil.rmtree(out)

    def test_sample_line_shape(self):
        # a sample line is gzipped JSON with the epoch key the readout joins
        # placement records against
        sample = {"t": 123.45, "stat": WATCH.read_stat(PROC_STAT)}
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "samples.jsonl.gz")
            with gzip.open(path, "wb") as fp:
                fp.write((json.dumps(sample, separators=(",", ":")) + "\n").encode("utf-8"))
            with gzip.open(path, "rb") as fp:
                loaded = json.loads(fp.read().decode("utf-8"))
        self.assertEqual(loaded["t"], 123.45)
        self.assertEqual(loaded["stat"]["cpu0"][0], 10)


if __name__ == "__main__":
    unittest.main(verbosity=2)
