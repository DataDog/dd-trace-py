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
_JITTER_PATH = pathlib.Path(__file__).resolve().parents[2] / ".gitlab" / "benchmarks" / "steps" / "jitter.py"
_PREFLIGHT_PATH = pathlib.Path(__file__).resolve().parents[2] / ".gitlab" / "benchmarks" / "steps" / "preflight.py"


@pytest.fixture(scope="module")
def watch_mod():
    spec = importlib.util.spec_from_file_location("watch", _WATCH_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def jitter_mod():
    spec = importlib.util.spec_from_file_location("jitter", _JITTER_PATH)
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


PROC_SCHEDSTAT = """\
cpu0  5703795  940284  1316737
cpu1  5704001  940300  1316800
domain0 0 0 0
"""


def test_read_schedstat_rows(watch_mod):
    # T6: per-CPU /proc/schedstat (run-queue wait); the parser stays
    # column-agnostic because the field meanings moved across kernels.
    parsed = watch_mod.read_schedstat(PROC_SCHEDSTAT)
    assert parsed["0"] == [5703795, 940284, 1316737]
    assert parsed["1"] == [5704001, 940300, 1316800]
    # non-"cpuN" rows (sched domains) are dropped
    assert "domain0" not in parsed


def test_read_pid_schedstat(watch_mod):
    # T6: /proc/<pid>/schedstat triple -- time on cpu, run_delay, timeslices
    assert watch_mod.read_pid_schedstat("123456789 987654321 42\n") == [123456789, 987654321, 42]


PID_STATUS = """\
Name:\tpython3
State:\tR (running)
voluntary_ctxt_switches:\t152
nonvoluntary_ctxt_switches:\t7
"""


def test_read_pid_status_ctxt(watch_mod):
    # T6: the benchmark processes' preemption counters (H6 support)
    assert watch_mod.read_pid_status_ctxt(PID_STATUS) == {"voluntary": 152, "nonvoluntary": 7}
    # missing or malformed lines are skipped, never raised
    assert watch_mod.read_pid_status_ctxt("Name:\tpython3\nvoluntary_ctxt_switches:\tnope\n") == {}


def test_read_irq_per_cpu_count(watch_mod):
    # T6: /sys/kernel/irq/<N>/per_cpu_count -- the unmasked stand-in for
    # /proc/interrupts; offline/unknown entries parse to None
    counts = watch_mod.read_irq_per_cpu_count("0,0,123,0\n")
    assert counts == [0, 0, 123, 0]
    assert watch_mod.read_irq_per_cpu_count("0,,123,") == [0, None, 123, None]


def test_source_probe_shapes(watch_mod, tmp_path):
    # T6: every source read lands as {source, available, error}; empty counts
    # as not available (that is how /proc/interrupts is masked) and nothing
    # raises
    ok = tmp_path / "ok"
    ok.write_text("content\n")
    empty = tmp_path / "empty"
    empty.write_text("\n")
    missing = tmp_path / "missing"
    assert watch_mod.source_probe(ok) == {"source": str(ok), "available": True, "error": None}
    assert watch_mod.source_probe(empty) == {"source": str(empty), "available": False, "error": "empty"}
    probe = watch_mod.source_probe(missing)
    assert probe["available"] is False
    assert "missing" in probe["error"] or "No such" in probe["error"]


def test_perf_stat_events_are_user_only(run_mod):
    # T6: the perf stat wrap counts user space only -- perf_event_paranoid=2
    # in the unprivileged CI containers forbids kernel-inclusive counts
    events = run_mod.PERF_STAT_EVENTS.split(",")
    for name in ("cycles", "instructions", "cache-references", "cache-misses"):
        assert "%s:u" % name in events
    assert "task-clock" in events


def test_perf_event_attr_layout(watch_mod):
    # the attr struct must be exactly 64 bytes (PERF_ATTR_SIZE_VER0, through
    # config1) and set the flag bits perf_event_open(2) defines: the kernel
    # rejects sizes below VER0 with E2BIG, which is how the first T6 run
    # lost every perf counter (observed as "Argument list too long" on the
    # CI hosts).
    import ctypes

    attr = watch_mod.PerfEventAttr(watch_mod.PERF_TYPE_TRACEPOINT, 42, inherit=True)
    assert ctypes.sizeof(watch_mod.PerfEventAttr) == 64 == attr.size
    assert attr.type == watch_mod.PERF_TYPE_TRACEPOINT
    assert attr.config == 42
    assert attr.flags == 1 << 1  # inherit
    attr = watch_mod.PerfEventAttr(0, 0, exclude_kernel=True, read_format=3)
    assert attr.flags == 1 << 4
    assert attr.read_format == 3


def test_tracepoint_id_found_and_missing(watch_mod, tmp_path):
    # T6: irq:irq_handler_entry id resolution from tracingfs, via the
    # injectable bases so the test does not need a mounted host.
    events = tmp_path / "events" / "irq" / "irq_handler_entry"
    events.mkdir(parents=True)
    (events / "id").write_text("4242\n")
    assert watch_mod.tracepoint_id("irq/irq_handler_entry", bases=(tmp_path,)) == 4242
    assert watch_mod.tracepoint_id("irq/irq_handler_exit", bases=(tmp_path,)) is None


def test_pmu_event_config_assembles_format_masks(watch_mod, tmp_path):
    # T6: msr/smi event -> (type, config) assembled from the PMU's event
    # terms and format masks; the layout mirrors the real msr PMU sysfs.
    msr = tmp_path / "msr"
    (msr / "events").mkdir(parents=True)
    (msr / "format").mkdir(parents=True)
    (msr / "events" / "smi").write_text("event=0x00,umask=0x01\n")
    (msr / "format" / "event").write_text("config:0-7\n")
    (msr / "format" / "umask").write_text("config:8-15\n")
    (msr / "type").write_text("9\n")
    assert watch_mod.pmu_event_config("msr", "smi", devices=tmp_path) == (9, 0x100)
    assert watch_mod.pmu_event_config("msr", "tsc", devices=tmp_path) is None


def test_perf_probe_degrades_when_perf_missing(run_mod, monkeypatch, tmp_path):
    # T6: the perf-stat wrap must never block a benchmark -- with no perf
    # binary and no loadable watch module (ctypes fallback), the probe says
    # "none" and records why, instead of raising.
    monkeypatch.setattr(run_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(run_mod, "_load_watch_module", lambda: None)
    monkeypatch.setattr(run_mod, "_watch_module_error", "no watch.py under any of []")
    monkeypatch.setattr(run_mod, "_perf_probe_state", None)
    state = run_mod.perf_probe()
    assert state["mode"] == "none"
    assert "perf binary missing" in state["reason"]
    assert "no watch.py" in state["reason"]


def test_load_watch_module_via_project_dir(run_mod, monkeypatch, tmp_path):
    # T6: the harness copies run.py next to the scenario it runs, so the
    # repo-relative path is wrong in CI; CI_PROJECT_DIR must find watch.py
    # (and the fallback error must name what was tried)
    fake_repo = tmp_path / "repo"
    (fake_repo / ".gitlab" / "benchmarks" / "steps").mkdir(parents=True)
    (fake_repo / ".gitlab" / "benchmarks" / "steps" / "watch.py").write_text(
        "PERF_TYPE_SOFTWARE = 1\nPERF_COUNT_SW_TASK_CLOCK = 1\n"
    )
    monkeypatch.setenv("CI_PROJECT_DIR", str(fake_repo))
    monkeypatch.chdir(tmp_path)
    # the harness copies run.py two levels deep in a scratch dir, so the
    # repo-relative candidate does not exist and only CI_PROJECT_DIR can win
    monkeypatch.setattr(run_mod, "__file__", str(tmp_path / "scratch" / "venv" / "run.py"))
    monkeypatch.setattr(run_mod, "_watch_module", None)
    monkeypatch.setattr(run_mod, "_watch_module_loaded", False)
    monkeypatch.setattr(run_mod, "_watch_module_error", "")
    watch = run_mod._load_watch_module()
    assert watch is not None
    assert watch.PERF_TYPE_SOFTWARE == 1
    monkeypatch.setattr(run_mod, "_watch_module", None)
    monkeypatch.setattr(run_mod, "_watch_module_loaded", False)
    monkeypatch.setattr(run_mod, "_watch_module_error", "")
    monkeypatch.delenv("CI_PROJECT_DIR")
    # no candidate exists under the copied run.py's location or the cwd: the
    # error records what was tried so the CI readout can explain the fallback
    assert run_mod._load_watch_module() is None
    assert "watch.py" in run_mod._watch_module_error


def test_ctypes_counter_event_ids(run_mod):
    # the fallback counter must ask perf_event_open for the same events
    # `perf stat -e` names; ids from perf_event_open(2)
    events = {name: (ptype, config) for name, ptype, config in run_mod.CtypesPerfCounter.EVENTS}
    assert events["task-clock"] == (1, 1)
    assert events["cycles"] == (0, 0)
    assert events["instructions"] == (0, 1)
    assert events["cache-references"] == (0, 2)
    assert events["cache-misses"] == (0, 3)
    assert events["context-switches"] == (1, 3)
    assert events["cpu-migrations"] == (1, 4)
    assert events["page-faults"] == (1, 2)


def test_calibrate_threshold_floor_and_multiple(jitter_mod):
    # T6 jitter probe: the threshold is the 5 us floor or 10x the loop's
    # median spacing, whichever is larger, so no host turns its own ordinary
    # iterations into "gaps"
    assert jitter_mod.calibrate_threshold([100, 120, 90], 5000) == 5000
    assert jitter_mod.calibrate_threshold([900, 1000, 1100], 5000) == 10000
    assert jitter_mod.calibrate_threshold([], 5000) == 5000


def test_histogram_buckets(jitter_mod):
    counts = jitter_mod.histogram([4999, 5000, 2000000, 2000001], (5000, 10000))
    assert counts == {"0-5000": 1, "5000-10000": 1, "10000-inf": 2}
    assert jitter_mod.histogram([], (1,)) == {"0-1": 0, "1-inf": 0}


def test_jitter_summarize(jitter_mod):
    # T6 jitter probe: one summary per CPU -- count, stolen time net of one
    # baseline iteration per gap, size histogram, and the inter-gap interval
    # histogram the H7 periodicity judgement reads
    gaps = [(0, 6000), (100_000_000, 600_000), (200_000_000, 7000)]  # (t_ns, gap_ns)
    summary = jitter_mod.summarize(gaps, 5000, 100, 1.0)
    assert summary["gap_count"] == 3
    assert summary["total_gap_ns"] == 6000 + 600_000 + 7000
    assert summary["total_stolen_ns"] == 6000 + 600_000 + 7000 - 3 * 100
    assert summary["stolen_fraction"] == summary["total_gap_ns"] / 1e9
    assert summary["size_histogram_ns"]["5000-10000"] == 2
    assert summary["interval_histogram_s"]["0.1-1.0"] == 2  # two 0.1 s inter-gap intervals
    assert summary["max_gap_ns"] == 600_000
    empty = jitter_mod.summarize([], 5000, 100, 1.0)
    assert empty["gap_count"] == 0
    assert empty["stolen_fraction"] == 0.0


def test_probe_cpu_unavailable_is_recorded(jitter_mod, monkeypatch):
    # a CPU the process cannot pin to is recorded {available, error}, never
    # raised -- the probe cannot fail the job
    def refuse(pid, cpus):
        raise OSError(1, "not permitted")

    monkeypatch.setattr(jitter_mod.os, "sched_setaffinity", refuse)
    record = jitter_mod.probe_cpu(24, 0.1, 0.1, 5000, 100)
    assert record["cpu"] == 24
    assert record["available"] is False
    assert "not permitted" in record["error"]


@pytest.fixture(scope="module")
def preflight_mod():
    spec = importlib.util.spec_from_file_location("preflight", _PREFLIGHT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_preflight_read_source_shapes(preflight_mod, tmp_path):
    # T6: the preflight's best-effort read records {source, available, error}
    # plus size/preview; empty (masked) reads stay available=True with bytes=0
    ok = tmp_path / "ok"
    ok.write_text("content")
    empty = tmp_path / "empty"
    empty.write_text("")
    missing = tmp_path / "missing"
    rec = preflight_mod.read_source(ok)
    assert (rec["available"], rec["error"], rec["bytes"]) == (True, None, 7)
    rec = preflight_mod.read_source(empty)
    assert (rec["available"], rec["bytes"]) == (True, 0)
    rec = preflight_mod.read_source(missing)
    assert rec["available"] is False
    assert rec["error"]


def test_preflight_probe_never_raises(preflight_mod):
    # the preflight runs before the benchmarks in the CI job and must never
    # raise, whatever the container allows -- every source here is missing
    # or unreadable in the test environment, which is exactly the point
    report = preflight_mod.probe([24, 25, 36, 37])
    assert report["records"]
    assert all("source" in r and "available" in r for r in report["records"])
    # a missing perf binary is recorded, not fatal
    assert report["perf"] in ("missing", None) or "/" in str(report["perf"])
