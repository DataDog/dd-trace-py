"""Tests for the CPU watch parsers (.gitlab/benchmarks/steps/watch.py) and the
placement log (benchmarks/base/run.py).

EXPERIMENT (do not merge): CPU-asymmetry probe (PR #20052 / APMSP-4059). The
watch reads /proc and /sys on the benchmark hosts; these tests pin its parsers
against fixture excerpts so counter shapes are guaranteed before the CI run.
"""

import gzip
import importlib.util
import json
import pathlib
import sys
import types
from unittest import mock

import pytest


_WATCH_PATH = pathlib.Path(__file__).resolve().parents[2] / ".gitlab" / "benchmarks" / "steps" / "watch.py"
_RUN_PATH = pathlib.Path(__file__).resolve().parents[2] / "benchmarks" / "base" / "run.py"


@pytest.fixture(scope="module")
def watch_mod():
    spec = importlib.util.spec_from_file_location("watch", _WATCH_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def run_mod():
    # run.py imports yaml at module scope only for its own config loading;
    # append_placement_record does not use it, so a stub keeps the test env
    # free of a benchmark-only requirement.
    yaml = types.ModuleType("yaml")
    spec = importlib.util.spec_from_file_location("benchmarks_base_run", _RUN_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules, {"yaml": yaml}):
        spec.loader.exec_module(module)
    return module


PROC_STAT = """\
cpu  100 0 200 5000 10 5 3 2 0 0
cpu0 10 0 20 500 1 0 1 0 0 0
cpu1 11 0 21 501 1 1 0 1 0 0
cpu2 12 0 22 502 1 2 0 0 0 0
intr 12345
ctxt 6789
"""


@pytest.mark.parametrize(
    "cpu, field, value",
    [
        (0, 0, 10),  # user
        (0, 5, 0),  # irq
        (0, 6, 1),  # softirq
        (1, 7, 1),  # steal
        (2, 8, 0),  # guest
    ],
)
def test_read_stat_per_cpu_fields(watch_mod, cpu, field, value):
    parsed = watch_mod.read_stat(PROC_STAT)
    assert parsed["cpu%d" % cpu][field] == value
    assert parsed["cpu"][0] == 100
    assert parsed["intr"] == 12345
    assert parsed["ctxt"] == 6789


PROC_INTERRUPTS = """\
           CPU0       CPU1       CPU2
  0:        10         0         0  IO-APIC   2-edge    timer
  1:         0        20         0  IO-APIC   1-edge    i8042
 27:      1000      2000      3000  PCI-MSI 512000-edge  eth0-rx-0
NMI:         2         1         0  Non-maskable interrupts
CAL:         3         0         0  Function call interrupts
ERR:         0
"""


def test_read_interrupts_rows_and_devices(watch_mod):
    parsed = watch_mod.read_interrupts(PROC_INTERRUPTS)
    assert parsed["cpus"] == ["CPU0", "CPU1", "CPU2"]
    assert parsed["rows"]["27"]["dev"] == "PCI-MSI 512000-edge eth0-rx-0"
    assert parsed["rows"]["27"]["counts"] == [1000, 2000, 3000]
    # IPI rows (CAL) carry the per-CPU counts the IPI-readability check needs
    assert parsed["rows"]["CAL"]["counts"] == [3, 0, 0]
    # a row without per-CPU counts (spurious ERR) is dropped
    assert "ERR" not in parsed["rows"]


PROC_SOFTIRQS = """\
                    CPU0       CPU1       CPU2
          HI:          1          2          3
       TIMER:         10         20         30
      NET_RX:          0          5          0
"""


def test_read_softirqs_rows(watch_mod):
    parsed = watch_mod.read_softirqs(PROC_SOFTIRQS)
    assert parsed["HI"] == [1, 2, 3]
    assert parsed["TIMER"] == [10, 20, 30]
    assert parsed["NET_RX"] == [0, 5, 0]


def test_read_psi(watch_mod):
    parsed = watch_mod.read_psi("some avg10=0.00 avg60=0.01 avg300=0.00 total=12345\nfull avg10=1.0 total=678\n")
    assert parsed["some"]["avg60"] == 0.01
    assert parsed["some"]["total"] == 12345
    assert parsed["full"]["total"] == 678


def test_read_vmstat_filters_by_prefix(watch_mod):
    vmstat = (
        "pgfault 100\n"
        "pgmajfault 2\n"
        "numa_hit 1000\n"
        "numa_miss 7\n"
        "numa_foreign 0\n"
        "unrelated_key 42\n"
        "pswpin 0\n"
        "workingset_refault 5\n"
    )
    parsed = watch_mod.read_vmstat(vmstat)
    assert parsed == {
        "pgfault": 100,
        "pgmajfault": 2,
        "numa_hit": 1000,
        "numa_miss": 7,
        "numa_foreign": 0,
        "pswpin": 0,
        "workingset_refault": 5,
    }


@pytest.mark.parametrize(
    "text, expected",
    [
        # new kernels: one key per line
        ("numa_hit 100\nnuma_miss 0\nnuma_foreign 0\n", {"numa_hit": 100, "numa_miss": 0, "numa_foreign": 0}),
        # old kernels: aligned columns, several pairs per line
        ("numa_hit                 100     numa_miss                0\n", {"numa_hit": 100, "numa_miss": 0}),
    ],
)
def test_read_numastat_layouts(watch_mod, text, expected):
    assert watch_mod.read_numastat(text) == expected


def _pid_stat_line(pid, comm, state="R", ppid=1, utime=100, stime=200, processor=39):
    # stat(5) layout: field 3 (state) lands at index 0 of the post-')' split,
    # fields 14/15 (utime/stime) at 11/12, field 39 (processor) at 36.
    fields = [str(i) for i in range(3, 39)]  # placeholders for fields 3..38
    fields[0] = state
    fields[1] = str(ppid)
    fields[11] = str(utime)
    fields[12] = str(stime)
    fields.append(str(processor))  # field 39
    return "%d (%s) %s\n" % (pid, comm, " ".join(fields))


@pytest.mark.parametrize(
    "comm, expected",
    [
        ("python3", ["python3", 1, 100, 200, 39]),
        # comm containing ')' and spaces must not break the split
        ("task (worker)", ["task (worker)", 1, 100, 200, 39]),
    ],
)
def test_read_proc_pid_stat(watch_mod, comm, expected):
    assert watch_mod.read_proc_pid_stat(_pid_stat_line(42, comm)) == expected


def test_read_proc_pid_stat_positions(watch_mod):
    line = _pid_stat_line(7, "sh", state="S", ppid=40, utime=11, stime=22, processor=5)
    assert watch_mod.read_proc_pid_stat(line) == ["sh", 40, 11, 22, 5]


def test_read_cpuinfo(watch_mod):
    cpuinfo = (
        "processor\t: 0\n"
        "model name\t: Intel(R) Xeon(R) Platinum 8175M CPU @ 2.50GHz\n"
        "cpu MHz\t\t: 2900.000\n"
        "microcode\t: 0x500002c\n"
        "\n"
        "processor\t: 1\n"
        "model name\t: Intel(R) Xeon(R) Platinum 8175M CPU @ 2.50GHz\n"
        "cpu MHz\t\t: 3100.000\n"
        "microcode\t: 0x500002c\n"
    )
    parsed = watch_mod.read_cpuinfo(cpuinfo)
    assert len(parsed) == 2
    assert parsed[1]["processor"] == 1
    assert parsed[0]["model name"] == "Intel(R) Xeon(R) Platinum 8175M CPU @ 2.50GHz"
    assert parsed[1]["microcode"] == "0x500002c"


def test_read_cpuinfo_mhz(watch_mod):
    # per-processor current MHz: the readable substitute for scaling_cur_freq
    # in the benchmark containers
    cpuinfo = "processor\t: 24\ncpu MHz\t\t: 2100.000\n\nprocessor\t: 25\ncpu MHz\t\t: 2499.938\n"
    assert watch_mod.read_cpuinfo_mhz(cpuinfo) == {"24": 2100.0, "25": 2499.938}


def test_read_cpuinfo_mhz_ignores_bad_values(watch_mod):
    cpuinfo = "processor\t: 0\ncpu MHz\t\t: not-a-number\n"
    assert watch_mod.read_cpuinfo_mhz(cpuinfo) == {}


@pytest.mark.parametrize(
    "text, expected",
    [
        ("24-35", list(range(24, 36))),
        ("24-35,40", list(range(24, 36)) + [40]),
        ("0", [0]),
        ("", []),
    ],
)
def test_parse_cpulist(watch_mod, text, expected):
    assert watch_mod.parse_cpulist(text) == expected


def test_placement_record_roundtrip(watch_mod, run_mod, tmp_path):
    # The placement record is the join key between results.json (config names)
    # and the watch samples (t, per-CPU counters); keep the shapes in sync.
    out = tmp_path / "candidate"
    out.mkdir()
    run_mod.append_placement_record(str(out), "span-start", [24], 100.0, 105.0, 4242)
    run_mod.append_placement_record(str(out), "span-start-finish", [25], 106.0, 111.0, 4243)
    records = [json.loads(ln) for ln in (out / "placement.jsonl").read_text().splitlines()]
    assert records[0] == {
        "scenario": None,
        "side": "candidate",
        "config": "span-start",
        "cpus": [24],
        "start": 100.0,
        "end": 105.0,
        "pid": 4242,
    }
    assert records[1]["side"] == "candidate"
    assert records[1]["config"] == "span-start-finish"


def test_effective_cpu_affinity_side_override(run_mod, monkeypatch):
    # T5: BENCH_CPUS_<SIDE> replaces CPU_AFFINITY only for the matching side,
    # inferred from the output dir name (run-benchmarks.sh uses
    # "$ARTIFACTS_DIR/<side>"). Other sides keep the affinity run-benchmarks.sh
    # exported for them.
    monkeypatch.setenv("CPU_AFFINITY", "24-35")
    monkeypatch.setenv("BENCH_CPUS_BASELINE", "36")
    assert run_mod.effective_cpu_affinity("/artifacts/1-scenario/candidate") == "24-35"
    assert run_mod.effective_cpu_affinity("/artifacts/1-scenario/baseline/") == "36"
    monkeypatch.setenv("BENCH_CPUS_CANDIDATE", "25")
    assert run_mod.effective_cpu_affinity("/artifacts/1-scenario/candidate") == "25"
    assert run_mod.effective_cpu_affinity("/artifacts/1-scenario/baseline") == "36"


def test_sample_join_shapes(watch_mod, tmp_path):
    # a sample line must be a JSON object carrying the epoch key the readout
    # joins placement records against; keep the serialization contract tested.
    sample = {"t": 123.45, "stat": watch_mod.read_stat(PROC_STAT)}
    line = json.dumps(sample, separators=(",", ":")) + "\n"
    with gzip.open(tmp_path / "samples.jsonl.gz", "wb") as fp:
        fp.write(line.encode("utf-8"))
    with gzip.open(tmp_path / "samples.jsonl.gz", "rb") as fp:
        loaded = json.loads(fp.read().decode("utf-8"))
    assert loaded["t"] == 123.45
    assert loaded["stat"]["cpu0"][0] == 10
