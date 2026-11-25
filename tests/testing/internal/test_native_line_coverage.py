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
