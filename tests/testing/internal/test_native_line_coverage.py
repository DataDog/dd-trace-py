"""Tests for the native line collector's payload encoding (CPython 3.12+)."""

import os
import subprocess
import sys
import textwrap

import pytest


if sys.version_info < (3, 12):
    pytest.skip("native line coverage needs sys.monitoring", allow_module_level=True)
pytest.importorskip("ddtrace.internal.coverage._linecov")


def _run_isolated(script: str) -> None:
    # A subprocess: the collector registers a sys.monitoring tool and process-wide state.
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        env={**os.environ, "_DD_COVERAGE_NATIVE_LINES": "true"},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


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
