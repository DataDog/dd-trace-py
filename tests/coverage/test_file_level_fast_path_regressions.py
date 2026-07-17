import sys

import pytest


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Test specific to Python 3.12+ monitoring API")
@pytest.mark.subprocess(parametrize={"_DD_COVERAGE_FILE_LEVEL": ["true", "false"]})
def test_same_file_fast_path_preserves_late_dynamic_import_dependency():
    """Guard against skipping same-file PY_START events too aggressively.

    File-level optimizations may want to stop doing work once a file is already
    covered in the current context. That is only safe if later code objects in
    that same file can still publish their import dependency metadata. Here the
    dependency module is imported before the test contexts, so the second
    context only gets that module's import-time coverage if the dynamic import
    dependency edge from ``covered_then_dynamic_import.py`` is preserved.
    """
    import os
    from pathlib import Path

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install
    from tests.coverage.utils import _get_relpath_dict

    cwd_path = os.getcwd()
    include_path = Path(cwd_path + "/tests/coverage/included_path/")

    install(include_paths=[include_path], collect_import_time_coverage=True)

    # Imported before the test contexts: later contexts should only include it
    # when dependency tracking proves the executed code imported it.
    from tests.coverage.included_path import preimported_dependency  # noqa:F401
    from tests.coverage.included_path.covered_then_dynamic_import import cover_file_without_import
    from tests.coverage.included_path.covered_then_dynamic_import import import_preimported_dependency

    with ModuleCodeCollector.CollectInContext() as context_without_dynamic_import:
        assert cover_file_without_import() == "covered"
        without_dynamic_import = _get_relpath_dict(cwd_path, context_without_dynamic_import.get_covered_lines())

    with ModuleCodeCollector.CollectInContext() as context_with_dynamic_import:
        # Cover the file first. A too-aggressive file-level fast path would be tempted to suppress the rest of the
        # file's PY_START callbacks after this point.
        assert cover_file_without_import() == "covered"
        assert import_preimported_dependency() == 42
        with_dynamic_import = _get_relpath_dict(cwd_path, context_with_dynamic_import.get_covered_lines())

    covered_file = "tests/coverage/included_path/covered_then_dynamic_import.py"
    dependency_file = "tests/coverage/included_path/preimported_dependency.py"

    assert covered_file in without_dynamic_import
    assert dependency_file not in without_dynamic_import, "import-time coverage should not leak into unrelated contexts"

    assert covered_file in with_dynamic_import
    assert dependency_file in with_dynamic_import, "late dynamic import dependency must merge import-time coverage"
