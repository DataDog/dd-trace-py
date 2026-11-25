"""Tests for the native line collector's payload encoding and test records (CPython 3.12+)."""

import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


if sys.version_info < (3, 12):
    pytest.skip("native line coverage needs sys.monitoring", allow_module_level=True)
pytest.importorskip("ddtrace.internal.coverage._linecov")

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _run_isolated(script: str, cwd=None, env=None) -> str:
    # A subprocess: the collector registers a sys.monitoring tool and process-wide state.
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        env={**os.environ, "_DD_COVERAGE_NATIVE_LINES": "true", **(env or {})},
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


_RECORDING_SCRIPT = f"""
    import json
    import sys
    from unittest.mock import patch

    sys.path.insert(0, {str(_REPO_ROOT)!r})

    import pytest

    from ddtrace.testing.internal.pytest.plugin import TestOptPlugin
    from ddtrace.testing.internal.tracer_api.coverage import CoverageData
    from ddtrace.testing.internal.tracer_api.coverage import native_record_bitmaps
    from tests.testing.mocks import mock_api_client_settings
    from tests.testing.mocks import setup_standard_mocks

    records = []

    def put_test_coverage(self, test_run, coverage_data):
        # Natively, the plugin hands over each test's or execution's window as its record.
        workspace = self.manager.workspace_path
        if isinstance(coverage_data, CoverageData):
            bitmaps = coverage_data.get_coverage_bitmaps(relative_to=workspace)
        else:
            bitmaps = native_record_bitmaps(coverage_data, workspace)
        files = {{}}
        for path, bitmap in bitmaps:
            files[path] = sorted(
                i * 8 + b for i, byte in enumerate(bitmap) for b in range(8) if byte & (0x80 >> b)
            )
        records.append({{"span_id": test_run.span_id, "files": files}})

    with (
        patch(
            "ddtrace.testing.internal.session_manager.APIClient",
            return_value=mock_api_client_settings(auto_retries_enabled=True, coverage_enabled=True),
        ),
        setup_standard_mocks(),
        patch.object(TestOptPlugin, "_put_test_coverage", put_test_coverage),
    ):
        pytest.main(["--ddtrace", "-p", "no:cacheprovider", "-p", "no:randomly", "-q", "%s"])
    print(json.dumps(records))
"""


def test_hooks_around_the_executions_count_for_the_test(tmp_path) -> None:
    """The repo's hooks that run around a test's executions, outside each execution's window, run
    for the test: their lines are in its record. pytest calls the logreport hooks after an execution,
    and the logstart and logfinish hooks and protocol wrappers around all of them.
    """
    (tmp_path / "hooks_helper.py").write_text(
        "def on_logstart():\n"
        "    started = True\n"
        "    return started\n"
        "\n"
        "\n"
        "def on_logreport():\n"
        "    reported = True\n"
        "    return reported\n"
        "\n"
        "\n"
        "def on_logfinish():\n"
        "    finished = True\n"
        "    return finished\n"
        "\n"
        "\n"
        "def around_protocol():\n"
        "    wrapped = True\n"
        "    return wrapped\n"
    )
    (tmp_path / "conftest.py").write_text(
        "import pytest\n"
        "\n"
        "import hooks_helper\n"
        "\n"
        "\n"
        "def pytest_runtest_logstart(nodeid, location):\n"
        "    hooks_helper.on_logstart()\n"
        "\n"
        "\n"
        "def pytest_runtest_logreport(report):\n"
        "    hooks_helper.on_logreport()\n"
        "\n"
        "\n"
        "def pytest_runtest_logfinish(nodeid, location):\n"
        "    hooks_helper.on_logfinish()\n"
        "\n"
        "\n"
        "@pytest.hookimpl(hookwrapper=True)\n"
        "def pytest_runtest_protocol(item, nextitem):\n"
        "    hooks_helper.around_protocol()\n"
        "    yield\n"
    )
    (tmp_path / "test_one.py").write_text("def test_one():\n    assert True\n")
    output = _run_isolated(
        _RECORDING_SCRIPT % "test_one.py",
        cwd=tmp_path,
        env={"DD_API_KEY": "test-key", "DD_CIVISIBILITY_FLAKY_RETRY_ENABLED": "false"},
    )
    records = json.loads(output.strip().splitlines()[-1])
    assert len(records) == 1 and records[0]["span_id"] is not None, records
    files = records[0]["files"]
    helper = next(v for k, v in files.items() if k.endswith("hooks_helper.py"))
    conftest = next(v for k, v in files.items() if k.endswith("conftest.py"))
    assert {2, 3, 7, 8, 12, 13, 17, 18} <= set(helper), files
    assert {7, 11, 15, 20, 21} <= set(conftest), files


def test_each_execution_gets_its_own_record(tmp_path) -> None:
    """A retried test sends one coverage record per execution, each with the lines that execution
    ran: here the first run fails on one branch and its automatic retry passes on the other.
    """
    (tmp_path / "test_flaky.py").write_text(
        "from pathlib import Path\n"
        "\n"
        'MARKER = Path(__file__).with_name("ran_once")\n'
        "\n"
        "\n"
        "def test_flaky():\n"
        "    if not MARKER.exists():\n"
        '        MARKER.write_text("")\n'
        "        first = 1\n"
        "        assert first == 2\n"
        "    second = 2\n"
        "    assert second == 2\n"
    )
    output = _run_isolated(
        _RECORDING_SCRIPT % "test_flaky.py",
        cwd=tmp_path,
        env={
            "DD_API_KEY": "test-key",
            "DD_CIVISIBILITY_FLAKY_RETRY_ENABLED": "true",
            "DD_CIVISIBILITY_FLAKY_RETRY_COUNT": "5",
            "DD_CIVISIBILITY_DYNAMIC_ATR_ENABLED": "false",
            "_DD_CIVISIBILITY_OUT_OF_SESSION_RETRIES_ENABLED": "0",
        },
    )
    records = json.loads(output.strip().splitlines()[-1])
    span_ids = {record["span_id"] for record in records}
    assert None not in span_ids and len(span_ids) == len(records) == 2, records
    lines = [next(v for k, v in record["files"].items() if k.endswith("test_flaky.py")) for record in records]
    # Lines 1, 3 and 6 are the file's initialisers, which every test that runs the file's code gets.
    assert lines == [[1, 3, 6, 7, 8, 9, 10], [1, 3, 6, 7, 11, 12]], records


def test_new_parameter_of_a_suite_fixture_replaces_the_previous_setup(tmp_path) -> None:
    """A test that sets up a module-scoped fixture for a new parameter gets that setup's lines and not
    the previous parameter's, which was still cached when the test's setup started.
    """
    (tmp_path / "fixture_helper.py").write_text(
        textwrap.dedent(
            """\
            def set_up_a():
                a = 1
                return a


            def set_up_b():
                b = 2
                return b
            """
        )
    )
    (tmp_path / "test_param.py").write_text(
        "import pytest\n"
        "\n"
        "import fixture_helper\n"
        "\n"
        "\n"
        '@pytest.fixture(scope="module", params=["a", "b"])\n'
        "def resource(request):\n"
        '    if request.param == "a":\n'
        "        return fixture_helper.set_up_a()\n"
        "    return fixture_helper.set_up_b()\n"
        "\n"
        "\n"
        "def test_uses(resource):\n"
        "    assert resource\n"
    )
    output = _run_isolated(
        _RECORDING_SCRIPT % "test_param.py",
        cwd=tmp_path,
        env={"DD_API_KEY": "test-key", "DD_CIVISIBILITY_FLAKY_RETRY_ENABLED": "false"},
    )
    records = json.loads(output.strip().splitlines()[-1])
    assert len(records) == 2, records
    lines = [next(v for k, v in record["files"].items() if k.endswith("fixture_helper.py")) for record in records]
    # Lines 1 and 6 are the file's initialisers.
    assert lines == [[1, 2, 3, 6], [1, 6, 7, 8]], records


def test_a_test_skipped_before_it_starts_gets_no_suite_fixture(tmp_path) -> None:
    """A test a skip mark skips before its setup runs no code, so it gets no suite fixture's lines,
    even one that is set up already. A test that a fixture skips during its setup has started: it
    gets the suite fixtures its setup used.
    """
    (tmp_path / "fixture_helper.py").write_text("def set_up():\n    value = 1\n    return value\n")
    (tmp_path / "test_skips.py").write_text(
        "import pytest\n"
        "\n"
        "import fixture_helper\n"
        "\n"
        "\n"
        '@pytest.fixture(scope="module")\n'
        "def resource():\n"
        "    return fixture_helper.set_up()\n"
        "\n"
        "\n"
        "@pytest.fixture\n"
        "def stop():\n"
        '    pytest.skip("stops in setup")\n'
        "\n"
        "\n"
        "def test_first(resource):\n"
        "    assert resource\n"
        "\n"
        "\n"
        '@pytest.mark.skip(reason="skipped before it starts")\n'
        "def test_marked(resource):\n"
        "    assert resource\n"
        "\n"
        "\n"
        "def test_stopped(resource, stop):\n"
        "    assert resource\n"
    )
    output = _run_isolated(
        _RECORDING_SCRIPT % "test_skips.py",
        cwd=tmp_path,
        env={"DD_API_KEY": "test-key", "DD_CIVISIBILITY_FLAKY_RETRY_ENABLED": "false"},
    )
    records = json.loads(output.strip().splitlines()[-1])
    assert len(records) == 3, records
    helper = [
        next((v for k, v in record["files"].items() if k.endswith("fixture_helper.py")), None) for record in records
    ]
    assert helper == [[1, 2, 3], None, [1, 2, 3]], records


def test_nested_windows_keep_no_lines_armed(tmp_path) -> None:
    """The test's window, its execution's window and a suite fixture's setup window nest on one
    thread. Only the innermost one keeps lines armed, so loops run with their lines disabled after
    the first pass, and each record still has every line.
    """
    (tmp_path / "work.py").write_text(
        textwrap.dedent(
            """\
            def work(n):
                total = 0
                for i in range(n):
                    total += i
                return total
            """
        )
    )
    (tmp_path / "conftest.py").write_text(
        textwrap.dedent(
            """\
            import json

            import pytest

            import work


            @pytest.fixture(scope="module")
            def resource():
                return work.work(500)


            def pytest_sessionfinish(session):
                from ddtrace.internal.coverage import _linecov

                with open("stats.json", "w") as f:
                    json.dump(_linecov.stats(), f)
            """
        )
    )
    (tmp_path / "test_nested.py").write_text(
        textwrap.dedent(
            """\
            import work


            def test_first(resource):
                assert work.work(500) == resource


            def test_second(resource):
                assert resource
            """
        )
    )
    output = _run_isolated(
        _RECORDING_SCRIPT % "test_nested.py",
        cwd=tmp_path,
        env={"DD_API_KEY": "test-key", "DD_CIVISIBILITY_FLAKY_RETRY_ENABLED": "false"},
    )
    records = json.loads(output.strip().splitlines()[-1])
    stats = json.loads((tmp_path / "stats.json").read_text())
    assert stats["events_kept"] == 0, stats
    assert len(records) == 2, records
    lines = [next(v for k, v in record["files"].items() if k.endswith("work.py")) for record in records]
    assert lines == [[1, 2, 3, 4, 5], [1, 2, 3, 4, 5]], records


def test_pack_matches_bitmaps(tmp_path) -> None:
    """pack() encodes what bitmaps(True) returns, including the longer msgpack forms: more than 15
    files, paths longer than 31 bytes and bitmaps longer than 255 bytes.
    """
    _run_isolated(
        f"""
        import os
        import sys

        from ddtrace.internal.coverage import native_lines
        from ddtrace.testing.internal.tracer_api import msgpack_packb

        sys.monitoring.use_tool_id(5, "test")
        assert native_lines.init(5)
        root = {str(tmp_path)!r}
        native_lines.set_report_root(root)
        functions = []
        for i in range(20):
            path = os.path.join(root, "a_package_directory_with_a_long_name", f"module_{{i}}.py")
            padding = "Z = 0\\n" * (2100 if i == 0 else 1)
            source = "X = 1\\n" + padding + "def f(x):\\n    y = x + 1\\n    return y\\n"
            code = compile(source, path, "exec")
            native_lines.instrument(code, path)
            namespace = {{}}
            exec(code, namespace)
            functions.append(namespace["f"])
        outside = compile("def g():\\n    return 1\\n", "/elsewhere/module.py", "exec")
        native_lines.instrument(outside, "/elsewhere/module.py")
        namespace = {{}}
        exec(outside, namespace)

        context = native_lines.new_context()
        previous = context.enter()
        for function in functions:
            function(1)
        namespace["g"]()
        context.exit(previous)

        bitmaps = context.bitmaps(True)
        count, packed = context.pack("filename", "bitmap")
        assert count == len(bitmaps) == 20, (count, len(bitmaps))
        assert max(len(bitmap) for _, bitmap in bitmaps) > 255
        assert packed == msgpack_packb([{{"filename": path, "bitmap": bitmap}} for path, bitmap in bitmaps])

        empty = native_lines.new_context()
        assert empty.pack("filename", "bitmap") == (0, msgpack_packb([]))
        """
    )


def test_import_in_event_loop_with_patched_os(tmp_path) -> None:
    """A module imported inside a running event loop is instrumented without calling the os and
    os.path functions that blocking-call detectors (blockbuster) patch to raise there.
    """
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "__init__.py").write_text("")
    (root / "sub" / "late").mkdir(parents=True)
    (root / "sub" / "late" / "__init__.py").write_text("")
    (root / "sub" / "late" / "work.py").write_text("def f(x):\n    y = x + 1\n    return y\n")
    # Imported through a symbolic link, so its path needs resolving.
    os.symlink(root / "sub", tmp_path / "linked_sub")
    _run_isolated(
        f"""
        import asyncio
        import os
        from pathlib import Path
        import sys

        from ddtrace.testing.internal.tracer_api import coverage

        root = os.path.realpath({str(root)!r})
        coverage.install_coverage(Path(root))
        sys.path.insert(0, root)
        sys.path.append(os.path.join(os.path.dirname(root), "linked_sub"))
        import pkg  # noqa: F401

        assert coverage.native_line_coverage_enabled()

        def refuse_in_event_loop(function):
            def wrapper(*args, **kwargs):
                try:
                    asyncio.get_running_loop()
                except RuntimeError:
                    return function(*args, **kwargs)
                raise RuntimeError("blocking call to " + function.__name__)

            return wrapper

        for name in ("lstat", "stat", "readlink", "getcwd"):
            setattr(os, name, refuse_in_event_loop(getattr(os, name)))
        for name in ("islink", "abspath", "realpath", "relpath"):
            setattr(os.path, name, refuse_in_event_loop(getattr(os.path, name)))

        async def run():
            from late import work

            return work.f(1)

        with coverage.coverage_collection() as data:
            assert asyncio.run(run()) == 2
        bitmaps = dict(data.get_coverage_bitmaps(Path(root)))
        bitmap = bitmaps["/sub/late/work.py"]
        lines = {{i * 8 + b for i, byte in enumerate(bitmap) for b in range(8) if byte & (0x80 >> b)}}
        assert lines == {{1, 2, 3}}, bitmaps
        coverage.uninstall_coverage()
        """
    )


_CHILD_SOURCES = {
    "pkg/__init__.py": "",
    "pkg/__main__.py": "import sys\n\nfrom pkg.cli import main\n\nsys.exit(main(sys.argv[1:]))\n",
    "pkg/cli.py": textwrap.dedent(
        """\
        import sys


        def main(argv):
            if argv[0] == "a":
                print("a")
            elif argv[0] == "b":
                print("b")
            elif argv[0] == "nested":
                import subprocess

                subprocess.run([sys.executable, "-m", "pkg", "a"], check=True)
            elif argv[0] == "wait":
                print("ready", flush=True)
                sys.stdin.readline()
            return 0
        """
    ),
    "scripts/tool.py": textwrap.dedent(
        """\
        def run():
            total = 0
            return total


        if __name__ == "__main__":
            run()
        """
    ),
}


def test_child_processes_and_runpy(tmp_path) -> None:
    """Python children started from a test's context report their lines to the test, whatever
    environment they are given; a child that outlives its test reports to the unowned lines; and
    module code run through runpy is recorded like imported code.
    """
    root = tmp_path / "repo"
    for name, source in _CHILD_SOURCES.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(source)
    _run_isolated(
        f"""
        import os
        from pathlib import Path
        import runpy
        import subprocess
        import sys

        from ddtrace.internal.coverage import native_lines
        from ddtrace.testing.internal.tracer_api import coverage

        root = os.path.realpath({str(root)!r})
        coverage.install_coverage(Path(root))
        # The collector claims its sys.monitoring tool when it instruments the first module.
        sys.path.insert(0, root)
        import pkg  # noqa: F401

        assert coverage.native_line_coverage_enabled()

        def lines(bitmaps, report_path):
            for path, bitmap in bitmaps:
                if path == report_path:
                    return {{i * 8 + b for i, byte in enumerate(bitmap) for b in range(8) if byte & (0x80 >> b)}}
            return set()

        def run_test(command, **kwargs):
            with coverage.coverage_collection() as data:
                subprocess.run([sys.executable, "-m", "pkg", command], cwd=root, check=True, **kwargs)
            return list(data.get_coverage_bitmaps(Path(root)))

        bitmaps = run_test("a")
        assert lines(bitmaps, "/pkg/cli.py") == {{1, 4, 5, 6, 16}}, bitmaps
        assert lines(bitmaps, "/pkg/__main__.py") == {{1, 3, 5}}, bitmaps
        directory = os.environ["_DD_COVERAGE_CHILD_DIR"]

        # A replaced environment, with str or bytes keys, and a grandchild.
        bitmaps = run_test("b", env={{"PATH": os.environ.get("PATH", "")}})
        assert lines(bitmaps, "/pkg/cli.py") == {{1, 4, 5, 7, 8, 16}}, bitmaps
        bitmaps = run_test("b", env={{b"PATH": os.environb.get(b"PATH", b"")}})
        assert lines(bitmaps, "/pkg/cli.py") == {{1, 4, 5, 7, 8, 16}}, bitmaps
        bitmaps = run_test("nested")
        assert lines(bitmaps, "/pkg/cli.py") == {{1, 4, 5, 6, 7, 9, 10, 12, 16}}, bitmaps

        # A child still running when its test ends reports to the unowned lines.
        with coverage.coverage_collection() as data:
            child = subprocess.Popen(
                [sys.executable, "-m", "pkg", "wait"], cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE
            )
            assert child.stdout.readline() == b"ready\\n"
        child.communicate(b"\\n")
        assert lines(list(data.get_coverage_bitmaps(Path(root))), "/pkg/cli.py") == set()
        shared = coverage.get_shared_coverage_bitmaps()
        assert lines(shared["unowned"], "/pkg/cli.py") == {{1, 4, 5, 7, 9, 13, 14, 15, 16}}, shared

        # runpy runs code it compiled itself: the function lines are the test's, the module body
        # lines are initialiser lines, which the test gets for a file it touched.
        with coverage.coverage_collection() as data:
            runpy.run_path(os.path.join(root, "scripts", "tool.py"), run_name="__main__")
        assert lines(list(data.get_coverage_bitmaps(Path(root))), "/scripts/tool.py") == {{1, 2, 3, 6, 7}}

        coverage.uninstall_coverage()
        assert "_DD_COVERAGE_CHILD_DIR" not in os.environ
        assert native_lines._BOOTSTRAP_DIR not in os.environ.get("PYTHONPATH", "")
        assert not os.path.exists(directory)
        """
    )


_MULTIPROCESSING_WORK = textwrap.dedent(
    """\
    import os


    def write(path, text):
        with open(path, "w") as f:
            f.write(text)


    def square(x):
        return x * x


    def cube(x):
        return x * x * x


    def wait(started, event):
        started.set() or event.wait(60)
        return os.getpid()


    def nested(method, path):
        import multiprocessing

        child = multiprocessing.get_context(method).Process(target=write, args=(path, method))
        child.start()
        child.join(60)
        return child.exitcode
    """
)


def test_multiprocessing_children_run_for_the_test(tmp_path) -> None:
    """multiprocessing children report their lines to the test that started them, on every start
    method: processes, pools that a with block terminates, pools closed and joined, the default
    process pool executor, a process the test terminates, and the processes a child starts. A
    child that outlives its test reports to the unowned lines.
    """
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "__init__.py").write_text("")
    (root / "pkg" / "work.py").write_text(_MULTIPROCESSING_WORK)
    _run_isolated(
        f"""
        import concurrent.futures
        import multiprocessing
        from multiprocessing import spawn
        import os
        from pathlib import Path
        import signal
        import sys

        from ddtrace.testing.internal.tracer_api import coverage

        root = os.path.realpath({str(root)!r})
        coverage.install_coverage(Path(root))
        sys.path.insert(0, root)
        from pkg import work

        assert coverage.native_line_coverage_enabled()
        # The initialiser lines of work.py, which a test that touches the file gets.
        init = {{1, 4, 9, 13, 17, 22}}

        def lines(bitmaps):
            for path, bitmap in bitmaps:
                if path == "/pkg/work.py":
                    return {{i * 8 + b for i, byte in enumerate(bitmap) for b in range(8) if byte & (0x80 >> b)}}
            return set()

        def run_test(function, *args):
            with coverage.coverage_collection() as data:
                function(*args)
            return lines(data.get_coverage_bitmaps(Path(root)))

        def process(method):
            target = os.path.join(root, method + ".txt")
            child = multiprocessing.get_context(method).Process(target=work.write, args=(target, method))
            child.start()
            child.join(60)
            assert child.exitcode == 0, child.exitcode

        def pool_with_block(method):
            # Leaving the block terminates the workers. One worker, which has started by then: CPython
            # drops a SIGTERM that reaches a fork child before its after-fork setup, and terminate()
            # would wait for that worker forever.
            with multiprocessing.get_context(method).Pool(1) as pool:
                assert pool.map(work.square, [2, 3]) == [4, 9]

        def pool_closed(method):
            pool = multiprocessing.get_context(method).Pool(2)
            assert pool.map(work.cube, [2, 3]) == [8, 27]
            pool.close()
            pool.join()

        def terminated(method):
            context = multiprocessing.get_context(method)
            started, event = context.Event(), context.Event()
            child = context.Process(target=work.wait, args=(started, event))
            child.start()
            # Line 18 has started: it sets started, then waits.
            assert started.wait(60)
            child.terminate()
            child.join(60)
            assert child.exitcode == -signal.SIGTERM, child.exitcode

        def nested(outer, inner):
            target = os.path.join(root, outer + "-" + inner + ".txt")
            child = multiprocessing.get_context(outer).Process(target=work.nested, args=(inner, target))
            child.start()
            child.join(60)
            assert child.exitcode == 0, child.exitcode
            assert Path(target).read_text() == inner

        for method in ("fork", "spawn", "forkserver"):
            assert run_test(process, method) == init | {{5, 6}}, method
            assert run_test(pool_with_block, method) == init | {{10}}, method
            assert run_test(pool_closed, method) == init | {{14}}, method
            assert run_test(terminated, method) == init | {{18}}, method

        # Not fork then forkserver: CPython fails it, as a fork child cannot use its parent's forkserver.
        for outer, inner in (("fork", "fork"), ("fork", "spawn"), ("spawn", "fork")):
            assert run_test(nested, outer, inner) == init | {{5, 6, 23, 25, 26, 27, 28}}, (outer, inner)

        def executor():
            with concurrent.futures.ProcessPoolExecutor(2) as pool:
                assert list(pool.map(work.cube, [2, 3])) == [8, 27]

        assert run_test(executor) == init | {{14}}

        # A child still running when its test ends reports to the unowned lines.
        context = multiprocessing.get_context("fork")
        started, event = context.Event(), context.Event()
        with coverage.coverage_collection() as data:
            child = context.Process(target=work.wait, args=(started, event))
            child.start()
            assert started.wait(60)
        event.set()
        child.join(60)
        assert lines(data.get_coverage_bitmaps(Path(root))) == set()
        assert lines(coverage.get_shared_coverage_bitmaps()["unowned"]) == {{18, 19}}

        # Native collection replaces the legacy child coverage: no pipe, nothing added for spawn children.
        assert "stowaway" not in spawn.get_preparation_data("child")
        assert getattr(multiprocessing.Process(), "_parent_conn", None) is None
        coverage.uninstall_coverage()
        """
    )


def test_exec_wrapper_behaves_like_exec() -> None:
    """The builtins.exec replacement passes module code to the callback, and is otherwise exec():
    same name, signature, documentation and pickling, and the caller's namespace without globals.
    """
    _run_isolated(
        """
        import builtins
        import inspect
        import pickle

        from ddtrace.internal.coverage import _linecov

        original = builtins.exec
        wrapper = _linecov.wrap_exec(original)
        assert _linecov.wrap_exec(original) is wrapper
        assert (wrapper.__name__, wrapper.__qualname__, wrapper.__module__) == ("exec", "exec", "builtins")
        assert inspect.signature(wrapper) == inspect.signature(original)
        assert wrapper.__doc__ == original.__doc__
        builtins.exec = wrapper
        assert pickle.loads(pickle.dumps(wrapper)) is wrapper

        seen = []
        _linecov.set_exec_callback(seen.append)
        code = compile("x = 1", "/nonexistent/module.py", "exec")
        exec(code)
        assert x == 1  # noqa: F821
        exec("y = 2")
        assert y == 2  # noqa: F821
        # Code compiled from a string has a "<...>" file name, and is not passed.
        assert seen == [code]

        def probe(run):
            namespace = {}
            run("caller = __name__", None, namespace)
            run("local = 3")
            return namespace["caller"], sorted(locals())

        assert probe(wrapper) == probe(original)

        _linecov.set_exec_callback(None)
        exec(code)
        assert seen == [code]
        """
    )


_MODELS_SOURCE = """\
import os

if os.environ.get("MODELS_CHECKING"):
    MODE = "checking"
else:
    MODE = "runtime"


def build(n):
    return list(range(n))
"""


def test_code_run_outside_the_import_hooks(tmp_path) -> None:
    """Module code that a loader compiles and runs itself is recorded like imported code: here a
    sys.meta_path finder ahead of the import hooks, and spec_from_file_location() with exec_module(),
    which runs the module body again on its other branch.
    """
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "__init__.py").write_text("")
    (root / "corpus").mkdir()
    (root / "corpus" / "models.py").write_text(_MODELS_SOURCE)
    _run_isolated(
        f"""
        import importlib.abc
        import importlib.util
        import os
        from pathlib import Path
        import sys

        from ddtrace.testing.internal.tracer_api import coverage

        root = os.path.realpath({str(root)!r})
        coverage.install_coverage(Path(root))
        # The collector claims its sys.monitoring tool when it instruments the first module.
        sys.path.insert(0, root)
        import pkg  # noqa: F401

        assert coverage.native_line_coverage_enabled()

        class SourceImporter(importlib.abc.MetaPathFinder, importlib.abc.Loader):
            # Compiles the sources up front and runs the code objects itself, as pydantic's
            # benchmark corpus importer does.
            def __init__(self, directory):
                self.codes = {{
                    "corpus_" + path.stem: compile(path.read_text(), str(path), "exec")
                    for path in Path(directory).glob("*.py")
                }}

            def find_spec(self, fullname, path=None, target=None):
                return importlib.util.spec_from_loader(fullname, self) if fullname in self.codes else None

            def create_module(self, spec):
                return None

            def exec_module(self, module):
                exec(self.codes[module.__name__], module.__dict__)

        sys.meta_path.insert(0, SourceImporter(os.path.join(root, "corpus")))

        def lines(bitmaps, report_path):
            for path, bitmap in bitmaps:
                if path == report_path:
                    return {{i * 8 + b for i, byte in enumerate(bitmap) for b in range(8) if byte & (0x80 >> b)}}
            return set()

        with coverage.coverage_collection() as data:
            import corpus_models

            corpus_models.build(2)
        assert lines(list(data.get_coverage_bitmaps(Path(root))), "/corpus/models.py") == {{1, 3, 6, 9, 10}}

        # The module body runs again under another name, on its other branch. Its lines join the
        # file's initialiser lines, which a test that touches the file afterwards gets.
        with coverage.coverage_collection():
            spec = importlib.util.spec_from_file_location("models_checking", os.path.join(root, "corpus", "models.py"))
            module = importlib.util.module_from_spec(spec)
            os.environ["MODELS_CHECKING"] = "1"
            spec.loader.exec_module(module)
            del os.environ["MODELS_CHECKING"]
        assert module.MODE == "checking"
        with coverage.coverage_collection() as data:
            corpus_models.build(3)
        assert lines(list(data.get_coverage_bitmaps(Path(root))), "/corpus/models.py") == {{1, 3, 4, 6, 9, 10}}

        coverage.uninstall_coverage()
        """
    )


_GENERATED_SOURCE = """\
def render(name):
    return "<p>" + name + "</p>"


result = render("x")
"""


def test_only_python_source_files_are_recorded(tmp_path) -> None:
    """Template engines such as Jinja compile code named after the template file, with the line
    numbers of the generated code, so only .py files are recorded, by the test and by its children.
    """
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "__init__.py").write_text("")
    (root / "pkg" / "helper.py").write_text(_GENERATED_SOURCE)
    (root / "templates").mkdir()
    (root / "templates" / "page.html").write_text("<p>{{ name }}</p>\n")
    _run_isolated(
        f"""
        import os
        from pathlib import Path
        import subprocess
        import sys

        from ddtrace.testing.internal.tracer_api import coverage

        root = os.path.realpath({str(root)!r})
        coverage.install_coverage(Path(root))
        # The collector claims its sys.monitoring tool when it instruments the first module.
        sys.path.insert(0, root)
        import pkg  # noqa: F401

        assert coverage.native_line_coverage_enabled()
        source = Path(root, "pkg", "helper.py").read_text()
        template = os.path.join(root, "templates", "page.html")
        helper = os.path.join(root, "pkg", "helper.py")
        # The same code compiled twice: under a template's name, as Jinja does, and under a .py name.
        run_both = "exec(compile(%r, %r, 'exec'), {{}}); exec(compile(%r, %r, 'exec'), {{}})" % (
            source,
            template,
            source,
            helper,
        )

        with coverage.coverage_collection() as data:
            exec(run_both, {{}})
        paths = [path for path, _ in data.get_coverage_bitmaps(Path(root))]
        assert "/pkg/helper.py" in paths and "/templates/page.html" not in paths, paths

        with coverage.coverage_collection() as data:
            subprocess.run([sys.executable, "-c", run_both], check=True)
        paths = [path for path, _ in data.get_coverage_bitmaps(Path(root))]
        assert "/pkg/helper.py" in paths and "/templates/page.html" not in paths, paths

        coverage.uninstall_coverage()
        """
    )


def test_isolated_asyncio_test_case_runs_in_the_test_context(tmp_path) -> None:
    """unittest's IsolatedAsyncioTestCase runs setUp, the test method and tearDown in a copy of the
    contextvars context taken when the test case is created, which pytest does at collection. Their
    lines are still the test's.
    """
    (tmp_path / "async_helper.py").write_text(
        "def in_set_up():\n"
        "    value = 1\n"
        "    return value\n"
        "\n"
        "\n"
        "async def in_test():\n"
        "    value = 2\n"
        "    return value\n"
        "\n"
        "\n"
        "def in_tear_down():\n"
        "    value = 3\n"
        "    return value\n"
    )
    (tmp_path / "test_async_case.py").write_text(
        "import unittest\n"
        "\n"
        "import async_helper\n"
        "\n"
        "\n"
        "class TestCase(unittest.IsolatedAsyncioTestCase):\n"
        "    async def asyncSetUp(self):\n"
        "        async_helper.in_set_up()\n"
        "\n"
        "    async def test_one(self):\n"
        "        await async_helper.in_test()\n"
        "\n"
        "    def tearDown(self):\n"
        "        async_helper.in_tear_down()\n"
    )
    output = _run_isolated(
        _RECORDING_SCRIPT % "test_async_case.py",
        cwd=tmp_path,
        env={"DD_API_KEY": "test-key", "DD_CIVISIBILITY_FLAKY_RETRY_ENABLED": "false"},
    )
    records = json.loads(output.strip().splitlines()[-1])
    assert len(records) == 1, records
    files = records[0]["files"]
    helper = next((v for k, v in files.items() if k.endswith("async_helper.py")), [])
    test = next((v for k, v in files.items() if k.endswith("test_async_case.py")), [])
    assert {2, 3, 7, 8, 12, 13} <= set(helper), files
    assert {8, 11, 14} <= set(test), files


def test_threads_a_test_starts_run_for_the_test(tmp_path) -> None:
    """A thread a test starts runs for the test. From Python 3.14 a thread runs its target in a
    context that its start() creates, empty by default. A context the caller passes to the thread is
    left untouched.
    """
    (tmp_path / "thread_helper.py").write_text(
        "import contextvars\n"
        "\n"
        "\n"
        "def in_thread(results):\n"
        "    value = 4\n"
        "    results.append(value)\n"
        "\n"
        "\n"
        "def context_names(names):\n"
        "    names.extend(var.name for var in contextvars.copy_context())\n"
    )
    (tmp_path / "test_threads.py").write_text(
        textwrap.dedent(
            """\
            import contextvars
            import json
            import sys
            import threading

            import pytest

            import thread_helper


            def test_plain_thread():
                results = []
                thread = threading.Thread(target=thread_helper.in_thread, args=(results,))
                thread.start()
                thread.join()
                assert results == [4]


            @pytest.mark.skipif(sys.version_info < (3, 14), reason="threads take a context from 3.14")
            def test_thread_with_its_own_context():
                names = []
                thread = threading.Thread(
                    target=thread_helper.context_names, args=(names,), context=contextvars.Context()
                )
                thread.start()
                thread.join()
                with open("names.json", "w") as f:
                    json.dump(names, f)
            """
        )
    )
    output = _run_isolated(
        _RECORDING_SCRIPT % "test_threads.py",
        cwd=tmp_path,
        env={"DD_API_KEY": "test-key", "DD_CIVISIBILITY_FLAKY_RETRY_ENABLED": "false"},
    )
    records = json.loads(output.strip().splitlines()[-1])
    helper = next((v for k, v in records[0]["files"].items() if k.endswith("thread_helper.py")), [])
    assert {5, 6} <= set(helper), records
    if sys.version_info >= (3, 14):
        assert json.loads((tmp_path / "names.json").read_text()) == []
