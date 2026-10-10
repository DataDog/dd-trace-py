"""Unit tests for probe.py (stdlib unittest, fixture-based, quick).

Run from the repo root or the steps directory:

    python3 .gitlab/benchmarks/steps/test_probe.py
"""

from pathlib import Path
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).parent))

import probe  # noqa: E402


CPUINFO_FIXTURE = """\
processor	: 0
vendor_id	: GenuineIntel
model name	: Intel(R) Xeon(R) Platinum 8259CL CPU @ 2.50GHz
cpu MHz		: 2500.000
processor	: 1
model name	: Intel(R) Xeon(R) Platinum 8259CL CPU @ 2.50GHz
"""

IRQ_EFFECTIVE_FIXTURE = {"79": "24-47", "81": "0-95", "107": "24-47,72-95"}


class ParseTests(unittest.TestCase):
    def test_parse_cpulist(self):
        self.assertEqual(probe.parse_cpulist("24-35,40,47"), [24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 40, 47])
        self.assertEqual(probe.parse_cpulist("24"), [24])
        self.assertEqual(probe.parse_cpulist(""), [])
        self.assertEqual(probe.parse_cpulist("garbage"), [])

    def test_parse_cache_size(self):
        self.assertEqual(probe.parse_cache_size("49152K"), 49152 * 1024)
        self.assertEqual(probe.parse_cache_size("36608K\n"), 36608 * 1024)
        self.assertEqual(probe.parse_cache_size("1024M"), 1024 * 1024 * 1024)
        self.assertEqual(probe.parse_cache_size("4096"), 4096)
        self.assertEqual(probe.parse_cache_size("junk"), 0)

    def test_read_l3_bytes_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            for idx, level, size in (("index0", "1", "32K"), ("index1", "3", "36608K"), ("index2", "3", "2048K")):
                d = base / idx
                d.mkdir()
                (d / "level").write_text(level)
                (d / "size").write_text(size)
            self.assertEqual(probe.read_l3_bytes(base), (36608 + 2048) * 1024)
            empty = base / "cpu-nope"
            empty.mkdir()
            self.assertIsNone(probe.read_l3_bytes(empty))

    def test_parse_cpuinfo_model(self):
        self.assertEqual(
            probe.parse_cpuinfo_model(CPUINFO_FIXTURE),
            "Intel(R) Xeon(R) Platinum 8259CL CPU @ 2.50GHz",
        )
        self.assertIsNone(probe.parse_cpuinfo_model("no model here"))

    def test_parse_irq_effective_affinity_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            for irq, aff in IRQ_EFFECTIVE_FIXTURE.items():
                d = base / irq
                d.mkdir()
                (d / "effective_affinity_list").write_text(aff + "\n")
            (base / "notanumber").mkdir()
            self.assertEqual(probe.parse_irq_effective_affinity(base), IRQ_EFFECTIVE_FIXTURE)
            self.assertEqual(probe.parse_irq_effective_affinity(base / "nope"), {})


class PythonCoreTests(unittest.TestCase):
    """Each core runs for a tiny window and returns positive ops."""

    def test_cores_quick(self):
        for name, fn in (
            ("int", lambda: probe.py_int(0.02)),
            ("alloc", lambda: probe.py_alloc(0.02)),
            ("fault", lambda: probe.py_fault(0.02)),
            ("simd", lambda: probe.py_memcpy(0.02, *probe.make_memcpy_bufs(1 << 20))),
            ("latency", lambda: probe.py_latency(0.02, probe.build_latency_table(1 << 20))),
        ):
            with self.subTest(scenario=name):
                ops, seconds = fn()
                self.assertGreater(ops, 0)
                self.assertGreater(seconds, 0.0)

    def test_rep_times(self):
        self.assertEqual(probe.rep_times([(100, 1.0), (200, 1.0), (0, 1.0)]), [0.01, 0.005])

    def test_spread_pct(self):
        self.assertAlmostEqual(probe.spread_pct([1.0, 1.1]), 100 * 0.1 / 1.05)
        self.assertEqual(probe.spread_pct([1.0]), 0.0)


class StatsTests(unittest.TestCase):
    @staticmethod
    def _per_cpu(slow_24=False):
        base = 1.0e-6
        out = {}
        for cpu in range(24, 29):
            t = base * 1.02 if cpu != 24 or not slow_24 else base * 1.4
            out[str(cpu)] = [t * 1.01, t * 0.99]  # ~2% rep spread
        return out

    def test_clean_when_flat(self):
        stats = probe.compute_scenario_stats(self._per_cpu())
        self.assertAlmostEqual(stats["host_median_s_per_op"], 1.02e-6, delta=1e-9)
        # ~2% rep spread -> 3x spread = 6%, above the 5% floor
        self.assertAlmostEqual(stats["flag_threshold_pct"], 6.0, delta=0.5)
        for entry in stats["cpus"].values():
            self.assertFalse(entry["flagged"])

    def test_cpu24_slow_by_40pct_flagged(self):
        stats = probe.compute_scenario_stats(self._per_cpu(slow_24=True))
        self.assertTrue(stats["cpus"]["24"]["flagged"])
        self.assertGreater(stats["cpus"]["24"]["deviation_pct"], 35.0)
        self.assertFalse(stats["cpus"]["25"]["flagged"])

    def test_high_rep_spread_raises_threshold(self):
        per_cpu = {str(c): [1.0e-6, 1.5e-6] for c in range(4)}  # 50% spread
        stats = probe.compute_scenario_stats(per_cpu)
        self.assertGreater(stats["flag_threshold_pct"], 100.0)
        self.assertFalse(any(e["flagged"] for e in stats["cpus"].values()))

    def test_single_cpu_no_deviation(self):
        stats = probe.compute_scenario_stats({"24": [1e-6, 1.1e-6]})
        self.assertTrue(stats["ran"])
        self.assertIsNone(stats["cpus"]["24"]["deviation_pct"])

    def test_verdicts(self):
        native = {s: "native" for s in probe.SCENARIOS}
        native["alloc"] = "python-workload"
        self.assertEqual(probe.decide_verdict({"pinned": True}, native, {}, []), "clean")
        self.assertEqual(
            probe.decide_verdict({"pinned": True}, native, {"24": [{"scenario": "alloc"}]}, []),
            "deviant",
        )
        self.assertEqual(probe.decide_verdict({"pinned": False}, native, {}, []), "inconclusive")
        broken = dict(native, fault="unavailable")
        self.assertEqual(probe.decide_verdict({"pinned": True}, broken, {}, []), "inconclusive")
        self.assertEqual(
            probe.decide_verdict({"pinned": True}, native, {}, ["budget: sweep stopped"]),
            "inconclusive",
        )


class NativeBuildTests(unittest.TestCase):
    def test_compile_and_run_native(self):
        binary, _toolchain, path = probe.compile_native()
        if binary is None:
            self.skipTest("no C compiler available")
        self.assertTrue(path.startswith(("found:", "installed:", "cached")), path)
        reps = probe.run_native(binary, "int", 0.02, 2)
        self.assertEqual(len(reps), 2)
        for ops, seconds in reps:
            self.assertGreater(ops, 0)
            self.assertGreater(seconds, 0)
        with self.assertRaises(probe.NativeError):
            probe.run_native(binary, "not-a-scenario", 0.01, 1)
        with self.assertRaises(probe.NativeError):
            probe.run_native(binary, "int", 0.01, 0)  # reps <= 0 -> rc 2

    def test_compiler_policy_install_path(self):
        """No preflight hit, one successful install, compiler then found."""
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "probe_native"
            original_find, original_install = probe._find_compiler, probe._install_gcc
            calls = []

            def fake_find():
                calls.append(1)
                return (None, None) if len(calls) == 1 else ("cc", "cc test 1.0")

            try:
                probe._find_compiler = fake_find
                probe._install_gcc = lambda: True
                result = probe.compile_native(binary_path=binary)
            finally:
                probe._find_compiler, probe._install_gcc = original_find, original_install
            if result[0] is None:
                self.skipTest("no C compiler available")
            self.assertEqual(result[2], "installed:cc")
            self.assertEqual(result[1], "cc test 1.0")

    def test_compiler_policy_falls_back_after_failed_install(self):
        original_find, original_install = probe._find_compiler, probe._install_gcc
        try:
            probe._find_compiler = lambda: (None, None)
            probe._install_gcc = lambda: False
            result = probe.compile_native(binary_path=Path("/nonexistent-dir/probe_native"))
        finally:
            probe._find_compiler, probe._install_gcc = original_find, original_install
        self.assertEqual(result, (None, None, "fallback-python"))

    def test_install_gcc_skipped_without_apt(self):
        # no apt-get on PATH -> the install is skipped without running anything
        original_which = probe.shutil.which
        try:
            probe.shutil.which = lambda name: None
            self.assertFalse(probe._install_gcc())
        finally:
            probe.shutil.which = original_which


class ProbeRunTests(unittest.TestCase):
    """End-to-end on the unpinned path (the macOS/local smoke path)."""

    def _tiny_probe(self, out, cpus=None):
        p = probe.Probe(Path(out), reps=1, rep_seconds=0.02, cpus=cpus)
        p.stream_bytes = 1 << 21
        p.latency_bytes = 1 << 21
        return p

    def test_run_unpinned_writes_inconclusive_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            probe_obj = self._tiny_probe(tmp, cpus=[])
            report = probe_obj.run()
            self.assertTrue((probe_obj.out_dir / "report.json").exists())
            md = (probe_obj.out_dir / "report.md").read_text()
            self.assertEqual(report["verdict"], "inconclusive")
            self.assertFalse(report["meta"]["pin"]["pinned"])
            for scenario in probe.SCENARIOS:
                self.assertIn(scenario, md)
                self.assertIn(scenario, report["scenarios"])
                fid = report["scenarios"][scenario]["fidelity"]
                self.assertIn(fid, ("native", "fallback-python", "python-workload"))
            if probe.compile_native()[0] is not None:
                self.assertEqual(report["scenarios"]["alloc"]["fidelity"], "python-workload")
            for scenario in probe.NATIVE_SCENARIOS:
                stats = report["scenarios"][scenario]
                self.assertTrue(stats["ran"], scenario)
                self.assertIn("unpinned", stats["cpus"])

    def test_fallback_fidelity_without_binary(self):
        with tempfile.TemporaryDirectory() as tmp:
            probe_obj = probe.Probe(Path(tmp), reps=1, rep_seconds=0.02)
            probe_obj.native_binary = None
            original = probe.compile_native
            try:
                probe.compile_native = lambda *a, **k: (None, None, "fallback-python")  # simulate no toolchain
                probe_obj._build_native()
            finally:
                probe.compile_native = original
            self.assertEqual(probe_obj.fidelity["alloc"], "python-workload")
            self.assertEqual(probe_obj.native_info["path"], "fallback-python")
            self.assertIn("no compiler", probe_obj.native_info["build"])
            for scenario in probe.NATIVE_SCENARIOS:
                self.assertEqual(probe_obj.fidelity[scenario], "fallback-python")

    def test_native_run_failure_downgrades_fidelity(self):
        with tempfile.TemporaryDirectory() as tmp:
            probe_obj = probe.Probe(Path(tmp), reps=1, rep_seconds=0.02)
            probe_obj.native_binary = "/nonexistent/probe_native"
            entry = probe_obj._run_scenario("int")
            self.assertIn("reps", entry)
            self.assertEqual(probe_obj.fidelity["int"], "fallback-python")
            self.assertIn("fallback-python", probe_obj.notes[0])

    def test_markdown_renders_deviant_cpu(self):
        report = {
            "verdict": "deviant",
            "meta": {
                "pin": {"probed_cpus": [24, 25]},
                "reps": 2,
                "rep_seconds": 1.5,
                "platform": "test",
                "tier": "local",
                "native": {"build": "ok"},
                "notes": [],
            },
            "static": {"cpu_model": "test cpu", "kernel": "k", "allowed_cpus": [24, 25], "l3_bytes": 1},
            "scenarios": {
                "alloc": {
                    "fidelity": "python-workload",
                    "unit": "objects",
                    "cpus": {
                        "24": {"median_s_per_op": 1.4e-7, "deviation_pct": 40.2, "flagged": True},
                        "25": {"median_s_per_op": 1.0e-7, "deviation_pct": -1.1, "flagged": False},
                    },
                }
            },
            "deviant_cpus": {"24": [{"scenario": "alloc", "deviation_pct": 40.2}]},
        }
        md = probe.render_markdown(report)
        self.assertIn("**deviant**", md)
        self.assertIn("CPU 24: alloc +40.2%", md)
        self.assertIn("**FLAG**", md)


if __name__ == "__main__":
    unittest.main()
