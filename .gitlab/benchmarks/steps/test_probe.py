"""Unit tests for probe.py (stdlib unittest, fixture-based, quick).

Run from the repo root or the steps directory:

    python3 .gitlab/benchmarks/steps/test_probe.py
"""

import ast
import inspect
from pathlib import Path
import sys
import tempfile
import types
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


class ScenarioContractTests(unittest.TestCase):
    """The probe's contract: names say what is measured, units match names."""

    def test_scenario_names_and_units(self):
        self.assertEqual(
            probe.SCENARIOS,
            (
                "int",
                "simd",
                "alloc",
                "fault",
                "stream",
                "mem-read",
                "mem-write",
                "gc-read",
                "gc-write",
                "slice",
                "slice-aspect",
            ),
        )
        self.assertEqual(
            probe.SCENARIO_UNITS,
            {
                "int": "iterations",
                "simd": "bytes",
                "alloc": "objects",
                "fault": "pages",
                "stream": "bytes",
                "mem-read": "accesses",
                "mem-write": "accesses",
                "gc-read": "collections",
                "gc-write": "batches",
                "slice": "iterations",
                "slice-aspect": "iterations",
            },
        )
        # no stragglers from the latency/gc renames or missing units
        self.assertEqual(set(probe.SCENARIO_UNITS), set(probe.SCENARIOS))
        self.assertTrue(set(probe.NATIVE_SCENARIOS) < set(probe.SCENARIOS))


class PythonCoreTests(unittest.TestCase):
    """Each core runs for a tiny window and returns positive ops."""

    def test_cores_quick(self):
        for name, fn in (
            ("int", lambda: probe.py_int(0.02)),
            ("alloc", lambda: probe.py_alloc(0.02)),
            ("fault", lambda: probe.py_fault(0.02)),
            ("simd", lambda: probe.py_memcpy(0.02, *probe.make_memcpy_bufs(1 << 20))),
            ("mem-read", lambda: probe.py_mem_read(0.02, probe.build_mem_read_table(1 << 20))),
            ("mem-write", lambda: probe.py_mem_write(0.02, probe.build_mem_write_table(1 << 20))),
            ("gc-read", lambda: probe.py_gc_read(0.02, 2000)),
            ("gc-write", lambda: probe.py_gc_write(0.02, pool_size=512)),
            ("slice", lambda: probe.py_slice(0.02, batch=4096)),
        ):
            with self.subTest(scenario=name):
                ops, seconds = fn()
                self.assertGreater(ops, 0)
                self.assertGreater(seconds, 0.0)

    def test_mem_read_table_is_one_full_cycle(self):
        # Sattolo's shuffle: the chase starting anywhere must visit every
        # entry before returning, so no rep can land in a cache-resident
        # short cycle (the bug behind the 54-74% rep spread of probe v1/v2)
        tbl = probe.build_mem_read_table(1 << 20)
        n = len(tbl)
        idx = 0
        length = 0
        while True:
            idx = tbl[idx]
            length += 1
            if idx == 0:
                break
        self.assertEqual(length, n)

    def test_mem_write_table_is_one_full_cycle(self):
        # mem-write's trail is the same Sattolo single cycle, over the
        # even (trail) slots of the interleaved pairs
        tbl = probe.build_mem_write_table(1 << 12)
        n = len(tbl) // 2
        idx = 0
        length = 0
        while True:
            idx = tbl[idx]
            length += 1
            if idx == 0:
                break
        self.assertEqual(length, n)
        # the chase stays on trail (even) slots; the odd slots are targets
        self.assertEqual(sorted(tbl[0::2]), list(range(0, 2 * n, 2)))

    def test_py_mem_write_stores_and_preserves_trail(self):
        # name fidelity: the write path must actually store (slots change)
        # while the single-cycle trail is left untouched (writes hit the
        # slot sharing the line, never the trail entry)
        tbl = probe.build_mem_write_table(1 << 12)
        before = list(tbl)
        ops, seconds = probe.py_mem_write(0.02, tbl)
        self.assertGreater(ops, 0)
        self.assertGreater(seconds, 0.0)
        self.assertNotEqual(list(tbl[1::2]), before[1::2])
        self.assertEqual(list(tbl[0::2]), before[0::2])

    def test_rep_times(self):
        self.assertEqual(probe.rep_times([(100, 1.0), (200, 1.0), (0, 1.0)]), [0.01, 0.005])

    def test_spread_pct(self):
        self.assertAlmostEqual(probe.spread_pct([1.0, 1.1]), 100 * 0.1 / 1.05)
        self.assertEqual(probe.spread_pct([1.0]), 0.0)


class GCReadCoreTests(unittest.TestCase):
    """The gc-read scenario's graph: deterministic, cyclic, and collectable."""

    def test_build_gc_graph_deterministic(self):
        g1 = probe.build_gc_graph(64)
        g2 = probe.build_gc_graph(64)
        self.assertEqual(len(g1), 64)
        self.assertEqual([n.payload for n in g1], [n.payload for n in g2])
        # both builds follow the same cross-reference pattern
        for i, node in enumerate(g1):
            self.assertIs(node.refs[0], g1[(i * 7 + 3) % 64])
            self.assertIs(node.refs[1], g1[(i * 31 + 11) % 64])
            self.assertIs(node.refs[2], g1[(i + 5) % 64])
        self.assertEqual([n.refs[0].payload for n in g1], [n.refs[0].payload for n in g2])

    def test_graph_is_cyclic(self):
        # every node has 3 cross-references and following refs[0] must come
        # back to the start: the permutation (i*7+3) % n forms real cycles
        # the collector has to chase
        nodes = probe.build_gc_graph(64)
        cur = nodes[0]
        for _ in range(64):
            cur = cur.refs[0]
            if cur is nodes[0]:
                break
        else:
            self.fail("refs[0] chain never returned to the starting node")

    def test_py_gc_read_runs_and_returns_collections(self):
        ops, seconds = probe.py_gc_read(0.05, n=3000)
        self.assertGreater(ops, 0)
        self.assertGreater(seconds, 0.0)


class GCWriteCoreTests(unittest.TestCase):
    """The gc-write scenario: churn in a recycled pool, no forced collect."""

    def test_py_gc_write_runs_and_returns_batches(self):
        ops, seconds = probe.py_gc_write(0.05, pool_size=512)
        self.assertGreater(ops, 0)
        self.assertGreater(seconds, 0.0)

    def test_py_gc_write_never_forces_collect(self):
        # name fidelity: gc-read times forced collections; gc-write must
        # not -- a counting stand-in has to see zero gc.collect() calls
        # while the churn runs
        import gc as gc_module

        calls = []
        original = gc_module.collect
        gc_module.collect = lambda *a, **k: calls.append(1)
        try:
            ops, _seconds = probe.py_gc_write(0.05, pool_size=512)
        finally:
            gc_module.collect = original
        self.assertGreater(ops, 0)
        self.assertEqual(calls, [])

    def test_py_gc_write_discards_and_relinks(self):
        # the churn itself: pool nodes get fresh payload strings and two
        # fresh satellites that cross-reference each other and the node
        pool = probe.build_gc_write_pool(512)
        before = [node.payload for node in pool]
        ops, _seconds = probe.py_gc_write(0.05, pool=pool)
        self.assertGreater(ops, 0)
        churned = 0
        for node in pool:
            if node.refs:
                self.assertEqual(len(node.refs), 3)
                a, b, _peer = node.refs
                self.assertIs(b.refs[0], a)  # the satellites cross-link
                self.assertIs(a.refs[1], node)
                churned += 1
        self.assertGreater(churned, 0)
        self.assertTrue(any(node.payload != p for node, p in zip(pool, before)))


class SliceScenarioTests(unittest.TestCase):
    """The slice scenario: the benchmark's own noaspect loop, verbatim.

    Structural only: these tests compare the copied loop against the
    benchmark source it was copied from, so drift in either file fails in
    CI. They never time anything.
    """

    REPO_ROOT = Path(__file__).resolve().parents[3]
    BENCH = REPO_ROOT / "benchmarks" / "appsec_iast_aspects_ospath"

    @staticmethod
    def _function_def(path, name):
        tree = ast.parse(Path(path).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return node
        raise AssertionError("%s not found in %s" % (name, path))

    def test_copied_function_is_verbatim(self):
        # the probe's ospathbasename_noaspect must be an exact AST copy of
        # the benchmark's functions.py:45-46: same signature, same body
        bench = self._function_def(self.BENCH / "functions.py", "ospathbasename_noaspect")
        copied = self._function_def(probe.__file__, "ospathbasename_noaspect")
        self.assertEqual(ast.dump(copied.args), ast.dump(bench.args))
        self.assertEqual([ast.dump(s) for s in copied.body], [ast.dump(s) for s in bench.body])

    def test_timed_loop_matches_benchmark_call_pattern(self):
        # scenario.py's noaspect branch (line 31) resolves the callee by
        # getattr on the functions module and discards the result into _;
        # the probe's timed batch must use that exact call shape
        scenario_src = (self.BENCH / "scenario.py").read_text()
        batch_src = inspect.getsource(probe._slice_batch)
        self.assertIn('_ = getattr(functions, "ospathbasename_noaspect")()', batch_src)
        # the benchmark's own call pattern, and the config that resolves
        # function_name for the noaspect config
        self.assertIn("_ = getattr(functions, self.function_name)()", scenario_src)
        config = (self.BENCH / "config.yaml").read_text()
        self.assertIn('function_name: "ospathbasename_noaspect"', config)

    def test_functions_mirror_is_a_module(self):
        # the benchmark getattr's on a real module, so the mirror must be
        # one; the copied function ignores its args exactly like the source
        functions = probe.build_slice_functions()
        self.assertIsInstance(functions, types.ModuleType)
        self.assertEqual(functions.ospathbasename_noaspect(), "file")

    def test_report_note_cites_source(self):
        # the report must say what was copied and from where, file:line
        with tempfile.TemporaryDirectory() as tmp:
            probe_obj = probe.Probe(Path(tmp), reps=1, rep_seconds=0.02)
            probe_obj.fidelity["slice"] = "python-workload"
            stats = probe_obj._scenario_stats("slice")
        self.assertIn("functions.py:45-46", stats["note"])
        self.assertIn("scenario.py:31", stats["note"])


class SliceAspectScenarioTests(unittest.TestCase):
    """slice-aspect: the benchmark's aspect machinery in the probe's harness.

    Structural only: the child's setup is compared against the benchmark
    source it mirrors (and the parent's plumbing against the child), so
    drift in either file fails in CI. The child is NEVER run here -- it
    needs a venv with the benchmark's ddtrace wheel; behavioral validation
    is CI-only, in the probe pass jobs.
    """

    REPO_ROOT = Path(__file__).resolve().parents[3]
    BENCH = REPO_ROOT / "benchmarks" / "appsec_iast_aspects_ospath"
    CHILD = Path(probe.__file__).parent / "probe_slice_aspect.py"

    @staticmethod
    def _module_with_block(path):
        # the FIRST module-level `with` -- the enable block in both files
        tree = ast.parse(Path(path).read_text())
        for node in tree.body:
            if isinstance(node, ast.With):
                return node
        raise AssertionError("no module-level with block in %s" % path)

    def test_enable_block_is_verbatim(self):
        # the child's enable block must be an exact AST copy of
        # scenario.py:11-15: IAST_ENV override, both asm_config flags,
        # enable_iast_propagation(), and `import functions` INSIDE the with
        # (that placement is what AST-patches the benchmark's functions.py)
        bench = self._module_with_block(self.BENCH / "scenario.py")
        child = self._module_with_block(self.CHILD)
        self.assertEqual(ast.dump(child), ast.dump(bench))

    def test_child_imports_mirror_scenario(self):
        # the imports the enable block needs are scenario.py:1-8's, verbatim;
        # bm imports before them so the watchdog never patches bm, same as
        # the benchmark (scenario.py:1 runs before the enable block)
        src = self.CHILD.read_text()
        for line in (
            "import bm",
            "from bm.iast_utils import IAST_ENV",
            "from bm.iast_utils import _with_iast_context",
            "from bm.iast_utils import _without_iast_context",
            "from bm.iast_utils import asm_config",
            "from bm.utils import override_env",
            "from ddtrace.appsec._iast import enable_iast_propagation",
        ):
            self.assertIn(line, src)

    def test_timed_call_patterns_match_benchmark(self):
        # scenario.py:29-31's per-iteration shape, with the two names
        # config.yaml resolves for the two configs; the child's generic
        # batch helper must use the exact call shape (getattr on the
        # functions module, result discarded into _) and only the names
        # vary, exactly like the benchmark loop's self.function_name
        child = self.CHILD.read_text()
        self.assertIn("_ = getattr(functions, function_name)()", child)
        self.assertIn('"ospathbasename_noaspect"', child)
        self.assertIn('"iast_ospathbasename_aspect"', child)
        self.assertIn("_ = getattr(functions, self.function_name)()", (self.BENCH / "scenario.py").read_text())
        config = (self.BENCH / "config.yaml").read_text()
        self.assertIn('function_name: "iast_ospathbasename_aspect"', config)
        self.assertIn('function_name: "ospathbasename_noaspect"', config)

    def test_variant_contexts_wrap_the_right_calls(self):
        # scenario.py:33-34: the noaspect config (iast_enabled: false) times
        # inside _without_iast_context, the aspect config inside
        # _with_iast_context; the child must wrap the matching variant the
        # same way, and the plain (main) variant must be measured before any
        # request context is ever started
        tree = ast.parse(self.CHILD.read_text())
        main = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "main")
        wrapped = {}
        for node in ast.walk(main):
            if isinstance(node, ast.With):
                ctx = ast.unparse(node.items[0].context_expr)
                names = wrapped.setdefault(ctx, set())
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Constant) and sub.value in (
                        "ospathbasename_noaspect",
                        "iast_ospathbasename_aspect",
                    ):
                        names.add(sub.value)
        self.assertEqual(wrapped.get("_without_iast_context()"), {"ospathbasename_noaspect"})
        self.assertEqual(wrapped.get("_with_iast_context()"), {"iast_ospathbasename_aspect"})
        self.assertIn(
            "context = _with_iast_context if self.iast_enabled else _without_iast_context",
            (self.BENCH / "scenario.py").read_text(),
        )

    def test_engagement_check_is_structural(self):
        # engagement must be decided from the code object (a plain compile
        # of the same source vs the loaded module) and the _ddtrace_ prefix
        # the visitor injects, never from the timing
        child = self.CHILD.read_text()
        self.assertIn("co_code", child)
        self.assertIn('k.startswith("_ddtrace_")', child)
        self.assertIn("ast_patched", child)

    def test_report_note_cites_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            probe_obj = probe.Probe(Path(tmp), reps=1, rep_seconds=0.02)
            probe_obj.fidelity["slice-aspect"] = "ddtrace-child"
            stats = probe_obj._scenario_stats("slice-aspect")
        for cite in (
            "scenario.py:1-8",
            "scenario.py:11-15",
            "scenario.py:29-31",
            "scenario.py:33-34",
            "functions.py:41-42",
        ):
            self.assertIn(cite, stats["note"])
        self.assertIn("machinery_engaged", stats["note"])

    def test_stats_record_secondary_and_engagement(self):
        # the explicit aspect call gets its own per-CPU stats and the
        # engagement verdict is carried into the scenario stats, so a
        # not-engaged run can never be mistaken for a clean one
        with tempfile.TemporaryDirectory() as tmp:
            probe_obj = probe.Probe(Path(tmp), reps=2, rep_seconds=0.02)
            probe_obj.fidelity["slice-aspect"] = "ddtrace-child"
            probe_obj.results["slice-aspect"] = {
                "24": {
                    "reps": [(100, 1.0), (100, 1.0)],
                    "explicit_reps": [(100, 1.4), (100, 1.4)],
                    "engagement": {"ast_patched": True},
                },
                "25": {
                    "reps": [(100, 0.7), (100, 0.7)],
                    "explicit_reps": [(100, 1.0), (100, 1.0)],
                    "engagement": {"ast_patched": True},
                },
                "26": {
                    "reps": [(100, 0.7), (100, 0.7)],
                    "explicit_reps": [(100, 1.0), (100, 1.0)],
                    "engagement": {"ast_patched": True},
                },
            }
            stats = probe_obj._scenario_stats("slice-aspect")
        self.assertTrue(stats["machinery_engaged"]["engaged"])
        self.assertTrue(stats["cpus"]["24"]["ast_patched"])
        self.assertTrue(stats["cpus"]["24"]["flagged"])  # main: +43% over the host median
        self.assertFalse(stats["cpus"]["25"]["flagged"])
        self.assertTrue(stats["explicit_aspect"]["cpus"]["24"]["flagged"])  # explicit: +40%
        self.assertFalse(stats["explicit_aspect"]["cpus"]["25"]["flagged"])
        self.assertEqual(stats["explicit_aspect"]["unit"], "iterations")

    def test_markdown_renders_extras(self):
        report = {
            "verdict": "clean",
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
                "slice-aspect": {
                    "fidelity": "ddtrace-child",
                    "unit": "iterations",
                    "cpus": {
                        "24": {"median_s_per_op": 5.3e-6, "deviation_pct": 1.0, "flagged": False},
                        "25": {"median_s_per_op": 5.2e-6, "deviation_pct": -1.0, "flagged": False},
                    },
                    "machinery_engaged": {"engaged": True, "ast_patched": {"24": True, "25": True}},
                    "explicit_aspect": {
                        "unit": "iterations",
                        "cpus": {
                            "24": {"median_s_per_op": 5.4e-6, "deviation_pct": 1.2, "flagged": False},
                            "25": {"median_s_per_op": 5.3e-6, "deviation_pct": -1.2, "flagged": False},
                        },
                    },
                }
            },
            "deviant_cpus": {},
        }
        md = probe.render_markdown(report)
        self.assertIn("slice-aspect AST patch engaged: yes", md)
        self.assertIn("slice-aspect (explicit aspect call)", md)


class SliceAspectWheelTests(unittest.TestCase):
    """The aspect venv plumbing: wheel resolution and install policy.

    No behavioral runs: subprocess is faked or the no-wheel path is taken
    (no wheel exists outside the CI pass jobs). The child itself is never
    executed.
    """

    def test_wheel_resolution_prefers_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertIsNone(probe.find_aspect_wheel(root))
            (root / "baseline-wheel").mkdir()
            (root / "baseline-wheel" / "ddtrace-base-1.0-py3-none-any.whl").write_text("")
            self.assertEqual(probe.find_aspect_wheel(root).name, "ddtrace-base-1.0-py3-none-any.whl")
            (root / "candidate-wheel").mkdir()
            (root / "candidate-wheel" / "ddtrace-cand-2.0-py3-none-any.whl").write_text("")
            # the candidate build is what the benchmark's candidate side runs
            self.assertEqual(probe.find_aspect_wheel(root).name, "ddtrace-cand-2.0-py3-none-any.whl")

    def test_no_wheel_records_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            venv_python, info = probe.prepare_slice_aspect(Path(tmp))
        self.assertIsNone(venv_python)
        self.assertIn("unavailable", info["state"])
        self.assertIn("no ddtrace wheel", info["state"])

    def test_install_runs_only_when_import_fails(self):
        """A cached importable venv is reused; pip runs exactly once when not."""
        import subprocess as subprocess_mod

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wheel_dir = root / "candidate-wheel"
            wheel_dir.mkdir()
            wheel = wheel_dir / "ddtrace-cand-2.0-cp312-cp312-manylinux2014_x86_64.whl"
            wheel.write_text("")
            fake_python = root / "target" / "slice-aspect-venv" / "bin" / "python"
            fake_python.parent.mkdir(parents=True)
            fake_python.write_text("#!/bin/sh\n")  # pre-created: venv step skipped
            calls = []

            def fake_run(cmd, **kwargs):
                if cmd[1:3] == ["-c", "import ddtrace.appsec._iast"]:
                    calls.append("import-check")
                    # import fails before install, succeeds after
                    return subprocess_mod.CompletedProcess(cmd, 0 if "pip" in calls else 1, stdout=b"", stderr=b"boom")
                if cmd[1:3] == ["-m", "pip"]:
                    calls.append("pip")
                    return subprocess_mod.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")
                raise AssertionError("unexpected command %r" % cmd)

            original_run = probe.subprocess.run
            probe.subprocess.run = fake_run
            try:
                venv_python, info = probe.prepare_slice_aspect(root)
            finally:
                probe.subprocess.run = original_run
        self.assertEqual(venv_python, fake_python)
        self.assertEqual(info["state"], "ok")
        self.assertEqual(info["wheel"], wheel.name)
        self.assertIsNotNone(info["install_s"])
        self.assertEqual(calls, ["import-check", "pip", "import-check"])  # imported twice, installed once

    def test_failed_install_records_reason(self):
        import subprocess as subprocess_mod

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wheel_dir = root / "candidate-wheel"
            wheel_dir.mkdir()
            (wheel_dir / "ddtrace-cand-2.0-cp312-cp312-manylinux2014_x86_64.whl").write_text("")
            fake_python = root / "target" / "slice-aspect-venv" / "bin" / "python"
            fake_python.parent.mkdir(parents=True)
            fake_python.write_text("#!/bin/sh\n")

            def fake_run(cmd, **kwargs):
                if cmd[1:3] == ["-c", "import ddtrace.appsec._iast"]:
                    return subprocess_mod.CompletedProcess(cmd, 1, stdout=b"", stderr=b"boom")
                if cmd[1:3] == ["-m", "pip"]:
                    return subprocess_mod.CompletedProcess(cmd, 2, stdout=b"", stderr=b"network on fire")
                raise AssertionError("unexpected command %r" % cmd)

            original_run = probe.subprocess.run
            probe.subprocess.run = fake_run
            try:
                venv_python, info = probe.prepare_slice_aspect(root)
            finally:
                probe.subprocess.run = original_run
        self.assertIsNone(venv_python)
        self.assertIn("pip install failed", info["state"])
        self.assertIn("network on fire", info["state"])


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

    def test_flag_requires_consistent_direction(self):
        # CPU 24 is +10% on the median but fast in one rep: threshold-boundary
        # flapping must stay unflagged even when the median exceeds 5%
        per_cpu = {"24": [1.4e-6, 0.8e-6]}
        for c in range(25, 29):
            per_cpu[str(c)] = [1.0e-6, 1.0e-6]
        stats = probe.compute_scenario_stats(per_cpu)
        self.assertFalse(stats["cpus"]["24"]["flagged"])
        self.assertFalse(stats["cpus"]["24"]["direction_consistent"])
        self.assertGreater(stats["cpus"]["24"]["deviation_pct"], 5.0)

    def test_flag_with_consistent_direction(self):
        per_cpu = {"24": [1.4e-6, 1.4e-6]}
        for c in range(25, 29):
            per_cpu[str(c)] = [1.0e-6, 1.0e-6]
        stats = probe.compute_scenario_stats(per_cpu)
        self.assertTrue(stats["cpus"]["24"]["flagged"])
        self.assertTrue(stats["cpus"]["24"]["direction_consistent"])

    def test_single_cpu_no_deviation(self):
        stats = probe.compute_scenario_stats({"24": [1e-6, 1.1e-6]})
        self.assertTrue(stats["ran"])
        self.assertIsNone(stats["cpus"]["24"]["deviation_pct"])

    def test_verdicts(self):
        native = {s: "native" for s in probe.SCENARIOS}
        native["alloc"] = "python-workload"
        native["gc-read"] = "python-workload"
        native["gc-write"] = "python-workload"
        native["slice"] = "python-workload"
        native["slice-aspect"] = "ddtrace-child"
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

    def test_native_mem_read_with_size(self):
        # the chase table is mmap'd + MADV_NOHUGEPAGE'd with an untimed
        # warm-up pass: verify the core runs and both reps report
        binary, _toolchain, _path = probe.compile_native()
        if binary is None:
            self.skipTest("no C compiler available")
        reps = probe.run_native(binary, "mem-read", 0.05, 2, 4 << 20)
        self.assertEqual(len(reps), 2)
        for ops, seconds in reps:
            self.assertGreater(ops, 0)
            self.assertGreater(seconds, 0)

    def test_native_mem_write_with_size(self):
        # the write-path twin: same trail shape, one store per step
        binary, _toolchain, _path = probe.compile_native()
        if binary is None:
            self.skipTest("no C compiler available")
        reps = probe.run_native(binary, "mem-write", 0.05, 2, 4 << 20)
        self.assertEqual(len(reps), 2)
        for ops, seconds in reps:
            self.assertGreater(ops, 0)
            self.assertGreater(seconds, 0)

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
        p.mem_bytes = 1 << 21
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
                # slice-aspect is "unavailable" wherever the pass-job wheel
                # artifacts are absent (everywhere but the CI pass jobs)
                self.assertIn(fid, ("native", "fallback-python", "python-workload", "ddtrace-child", "unavailable"))
            self.assertEqual(report["scenarios"]["slice-aspect"]["fidelity"], "unavailable")
            self.assertIn("no ddtrace wheel", report["meta"]["slice_aspect"]["state"])
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
            self.assertEqual(probe_obj.fidelity["gc-read"], "python-workload")
            self.assertEqual(probe_obj.fidelity["gc-write"], "python-workload")
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
