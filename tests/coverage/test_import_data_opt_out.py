"""Tests for the _DD_COVERAGE_INCLUDE_IMPORT_DATA opt-out.

The variable is private and enabled by default: per-test coverage reports include the
import-time coverage data of the files each test touches (their transitive import
closure). Setting _DD_COVERAGE_INCLUDE_IMPORT_DATA=false opts out of that augmentation,
which reduces the fixed per-test overhead on large codebases at the cost of test
skipping correctness (tests that import, but do not execute, changed code may not be
selected).
"""

import pytest


def test_is_import_data_enabled(monkeypatch):
    from ddtrace.internal.coverage.util import is_import_data_enabled

    monkeypatch.delenv("_DD_COVERAGE_INCLUDE_IMPORT_DATA", raising=False)
    assert is_import_data_enabled() is True

    for enabled_value in ("true", "1", "True"):
        monkeypatch.setenv("_DD_COVERAGE_INCLUDE_IMPORT_DATA", enabled_value)
        assert is_import_data_enabled() is True

    for disabled_value in ("false", "0", "False"):
        monkeypatch.setenv("_DD_COVERAGE_INCLUDE_IMPORT_DATA", disabled_value)
        assert is_import_data_enabled() is False


@pytest.mark.subprocess(
    parametrize={
        "_DD_COVERAGE_FILE_LEVEL": ["true", "false"],
        "_DD_COVERAGE_INCLUDE_IMPORT_DATA": ["true", "false"],
    },
    err=lambda err: err == "" or "Import-time coverage data is disabled" in err,
)
def test_collect_in_context_import_data_opt_out():
    import importlib
    import os
    from pathlib import Path
    import sys
    import tempfile
    import uuid

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install
    from ddtrace.internal.coverage.util import is_import_data_enabled
    from tests.coverage.utils import _get_relpath_dict

    import_data_enabled = is_import_data_enabled()

    # The application module imports the library at module level and only calls into the
    # application module from the coverage context, so the library can only appear in the
    # context's coverage through import-time data augmentation.
    suffix = uuid.uuid4().hex
    app_name = f"opt_out_app_{suffix}"
    lib_name = f"opt_out_lib_{suffix}"

    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir)
        (workspace / f"{lib_name}.py").write_text("VALUE = 42\n")
        (workspace / f"{app_name}.py").write_text(f"from {lib_name} import VALUE\n\n\ndef run():\n    return 1\n")

        sys.path.insert(0, str(workspace))
        try:
            install(include_paths=[workspace], collect_import_time_coverage=True)

            app_module = importlib.import_module(app_name)

            with ModuleCodeCollector.CollectInContext() as context:
                app_module.run()
                covered_lines = _get_relpath_dict(str(workspace), context.get_covered_lines())

            with ModuleCodeCollector.CollectInContext() as context:
                app_module.run()
                covered_files = {os.path.basename(path) for path in context.get_covered_file_paths()}

        finally:
            sys.path.remove(str(workspace))
            sys.modules.pop(app_name, None)
            sys.modules.pop(lib_name, None)

    app_file = f"{app_name}.py"
    lib_file = f"{lib_name}.py"

    # The executed application module is always covered, in line-level and file-level modes.
    assert app_file in covered_lines, f"Executed module missing from covered lines: {covered_lines}"
    assert app_file in covered_files, f"Executed module missing from covered files: {covered_files}"

    # The imported library is only covered when import-time data is included.
    if import_data_enabled:
        assert lib_file in covered_lines, f"Import data should be included in covered lines: {covered_lines}"
        assert lib_file in covered_files, f"Import data should be included in covered files: {covered_files}"
    else:
        assert lib_file not in covered_lines, f"Import data should be excluded from covered lines: {covered_lines}"
        assert lib_file not in covered_files, f"Import data should be excluded from covered files: {covered_files}"
