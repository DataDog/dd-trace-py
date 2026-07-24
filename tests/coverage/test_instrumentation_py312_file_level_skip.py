import sys

import pytest


pytestmark = pytest.mark.skipif(sys.version_info < (3, 12), reason="Python 3.12+ monitoring API only")


def test_file_level_import_extraction_skips_bytecode_without_imports(monkeypatch):
    import ddtrace.internal.coverage.instrumentation_py3_12 as m

    code = compile("x = 1\ny = x + 1", "<test_no_imports>", "exec")

    def fail_findlinestarts(_code):
        raise AssertionError("file-level import extraction should not scan line starts without import opcodes")

    monkeypatch.setattr(m.dis, "findlinestarts", fail_findlinestarts)

    lines, import_names = m._extract_lines_and_imports(code, "tests.coverage", track_lines=False)

    assert not lines
    assert import_names == {}


def test_file_level_import_extraction_keeps_import_metadata():
    import ddtrace.internal.coverage.instrumentation_py3_12 as m

    code = compile("import os\nfrom pathlib import Path", "<test_imports>", "exec")

    lines, import_names = m._extract_lines_and_imports(code, "tests.coverage", track_lines=False)

    assert not lines
    assert set(import_names.values()) == {("tests.coverage", ("os",)), ("tests.coverage", ("pathlib", "pathlib.Path"))}


def test_file_level_instrumentation_skips_only_eager_comprehensions(monkeypatch):
    import ddtrace.internal.coverage.instrumentation_py3_12 as m
    from ddtrace.internal.test_visibility.coverage_lines import CoverageLines

    code = compile(
        "values = [x for x in range(3)]\n"
        "mapping = {x: x for x in range(3)}\n"
        "unique = {x for x in range(3)}\n"
        "generator = (x for x in range(3))\n",
        "<test_comprehensions>",
        "exec",
    )
    instrumented_nested_names = []

    def fake_set_local_events(*args, **kwargs):
        pass

    def fake_instrument_all_lines(nested_code, hook, path, package):
        instrumented_nested_names.append(nested_code.co_name)
        return nested_code, CoverageLines()

    old_file_level = m._USE_FILE_LEVEL_COVERAGE
    old_tool_id = m._DD_TOOL_ID
    monkeypatch.setattr(m.sys.monitoring, "set_local_events", fake_set_local_events)
    monkeypatch.setattr(m, "instrument_all_lines", fake_instrument_all_lines)
    m._USE_FILE_LEVEL_COVERAGE = True
    m._DD_TOOL_ID = 1

    try:
        m._instrument_with_monitoring(code, lambda _: None, "/repo/test.py", "tests.coverage")
    finally:
        m._USE_FILE_LEVEL_COVERAGE = old_file_level
        m._DD_TOOL_ID = old_tool_id

    assert instrumented_nested_names == ["<genexpr>"]
