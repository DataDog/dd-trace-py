"""The native line collector while a test has the standard library patched (CPython 3.12+).

Tests routinely patch the standard library while they run: pyfakefs replaces os, os.path, open and
pathlib with a fake file system, and freezegun replaces the clocks of time and datetime. The
collector's per-test path depends on none of them.
"""

import os
import subprocess
import sys
import textwrap

import pytest


if sys.version_info < (3, 12):
    pytest.skip("native line coverage needs sys.monitoring", allow_module_level=True)
pytest.importorskip("ddtrace.internal.coverage._linecov")

_SOURCES = {
    "pkg/__init__.py": "",
    "pkg/__main__.py": "from pkg.cli import main\n\nmain()\n",
    "pkg/cli.py": "def main():\n    total = 0\n    for i in range(3):\n        total += i\n    return total\n",
    "pkg/late.py": "def f(x):\n    y = x + 1\n    return y\n",
    "pkg/work.py": "def f(x):\n    y = x * 2\n    return y\n\n\ndef g():\n    return 1\n",
}

# The script prelude: install the collector on the workspace, and read a file's lines from bitmaps.
_PRELUDE = """
import os
from pathlib import Path
import subprocess
import sys

from ddtrace.testing.internal.tracer_api import coverage

root = os.path.realpath(ROOT)
coverage.install_coverage(Path(root))
sys.path.insert(0, root)
import pkg  # noqa: F401

assert coverage.native_line_coverage_enabled()


def lines(bitmaps, report_path):
    for path, bitmap in bitmaps:
        if path == report_path:
            return {i * 8 + b for i, byte in enumerate(bitmap) for b in range(8) if byte & (0x80 >> b)}
    return set()


def run_child():
    subprocess.run([sys.executable, "-m", "pkg"], cwd=root, check=True)
"""

_CLI_LINES = {1, 2, 3, 4, 5}


def _run(tmp_path, script: str) -> None:
    root = tmp_path / "repo"
    for name, source in _SOURCES.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(source)
    # A subprocess: the collector registers a sys.monitoring tool and process-wide state.
    result = subprocess.run(
        [sys.executable, "-c", f"ROOT = {str(root)!r}\n" + _PRELUDE + textwrap.dedent(script)],
        env={**os.environ, "_DD_COVERAGE_NATIVE_LINES": "true"},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_fake_file_system(tmp_path) -> None:
    """A test keeps its lines, and those of its child processes, while pyfakefs fakes the file
    system: when the window closes on a fake file system that fixtures keep across tests, when the
    whole window runs on one and imports a module, and when a child exits with one still on.
    """
    pytest.importorskip("pyfakefs")
    _run(
        tmp_path,
        f"""
        from pyfakefs.fake_filesystem_unittest import Patcher

        with coverage.coverage_collection() as data:
            run_child()
            patcher = Patcher()
            patcher.setUp()
        try:
            bitmaps = list(data.get_coverage_bitmaps(Path(root)))
        finally:
            patcher.tearDown()
        assert lines(bitmaps, "/pkg/cli.py") == {_CLI_LINES}, bitmaps

        with Patcher():
            with coverage.coverage_collection() as data:
                from pkg import late

                assert late.f(1) == 2
            bitmaps = list(data.get_coverage_bitmaps(Path(root)))
        assert lines(bitmaps, "/pkg/late.py") == {{1, 2, 3}}, bitmaps

        program = (
            "from pkg.cli import main; from pyfakefs.fake_filesystem_unittest import Patcher; "
            "Patcher().setUp(); main()"
        )
        with coverage.coverage_collection() as data:
            subprocess.run([sys.executable, "-c", program], cwd=root, check=True)
        bitmaps = list(data.get_coverage_bitmaps(Path(root)))
        assert lines(bitmaps, "/pkg/cli.py") == {_CLI_LINES}, bitmaps
        coverage.uninstall_coverage()
        """,
    )


def test_frozen_time(tmp_path) -> None:
    """A test keeps its lines, those of its threads and those of its child processes while
    freezegun freezes the clocks: for the whole window, and from the middle of one window to the
    middle of the next.
    """
    pytest.importorskip("freezegun")
    _run(
        tmp_path,
        f"""
        import threading

        from freezegun import freeze_time

        def run_test():
            from pkg import work

            assert work.f(2) == 4
            thread = threading.Thread(target=work.g)
            thread.start()
            thread.join()
            run_child()

        def check(bitmaps):
            assert lines(bitmaps, "/pkg/work.py") == {{1, 2, 3, 6, 7}}, bitmaps
            assert lines(bitmaps, "/pkg/cli.py") == {_CLI_LINES}, bitmaps

        with freeze_time("2001-02-03 04:05:06"):
            with coverage.coverage_collection() as data:
                run_test()
            check(list(data.get_coverage_bitmaps(Path(root))))

        frozen = freeze_time("1999-12-31 23:59:59")
        with coverage.coverage_collection() as first:
            run_test()
            frozen.start()
        with coverage.coverage_collection() as second:
            run_test()
            frozen.stop()
        check(list(first.get_coverage_bitmaps(Path(root))))
        check(list(second.get_coverage_bitmaps(Path(root))))
        coverage.uninstall_coverage()
        """,
    )
