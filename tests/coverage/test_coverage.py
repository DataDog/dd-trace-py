"""This file includes various tests that exercise the internal coverage collection module

Tests use the subprocess pytest mark to ensure that coverage collection happens in a clean environment.

Tests that cover import-time dependencies are meant to catch issues (important to the Intelligent Test Runner) with
lines that code technically depends on (eg: imported functions, classes, or constants), but are executed at import
time rather than at code execution time.
"""

import sys

import pytest


def test_coverage_stacks_are_isolated_across_copied_contexts():
    from contextvars import copy_context

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.code import ctx_covered
    from ddtrace.internal.coverage.code import ctx_covered_files

    with ModuleCodeCollector.CollectInContext():
        parent_lines_stack = ctx_covered.get()
        parent_files_stack = ctx_covered_files.get()
        parent_depth = len(parent_lines_stack)
        child_context = copy_context()

        def collect_in_child_context():
            with ModuleCodeCollector.CollectInContext():
                assert len(ctx_covered.get()) == len(ctx_covered_files.get()) == parent_depth + 1
                assert ctx_covered.get()[-1] is not parent_lines_stack[-1]
                assert ctx_covered_files.get()[-1] is not parent_files_stack[-1]

        child_context.run(collect_in_child_context)
        assert ctx_covered.get() is parent_lines_stack
        assert ctx_covered_files.get() is parent_files_stack
        assert len(parent_lines_stack) == len(parent_files_stack) == parent_depth


def test_exiting_collector_in_another_context_preserves_active_coverage():
    from contextvars import copy_context

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.code import ctx_covered
    from ddtrace.internal.coverage.code import ctx_covered_files

    with ModuleCodeCollector.CollectInContext():
        parent_depth = len(ctx_covered.get())
        parent_lines = ctx_covered.get()[-1]
        parent_files = ctx_covered_files.get()[-1]
        child_context = copy_context()
        child = ModuleCodeCollector.CollectInContext()
        child_context.run(child.__enter__)

        child.__exit__()
        assert ctx_covered.get()[-1] is parent_lines
        assert ctx_covered_files.get()[-1] is parent_files

        child_context.run(child.__exit__)
        assert len(ctx_covered.get()) == len(ctx_covered_files.get()) == parent_depth


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Test specific to Python 3.12+ monitoring API")
@pytest.mark.subprocess()
def test_coverage_defaults_to_file_level_when_env_unset():
    import os
    from pathlib import Path

    os.environ.pop("_DD_COVERAGE_FILE_LEVEL", None)

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install
    from tests.coverage.utils import _get_relpath_dict

    cwd_path = os.getcwd()
    include_path = Path(cwd_path + "/tests/coverage/included_path/")

    install(include_paths=[include_path])

    from tests.coverage.included_path.lib import called_in_session

    with ModuleCodeCollector.CollectInContext() as context:
        called_in_session(1, 2)
        covered = _get_relpath_dict(cwd_path, context.get_covered_lines())

    assert covered["tests/coverage/included_path/lib.py"] == {0}


@pytest.mark.subprocess(parametrize={"_DD_COVERAGE_FILE_LEVEL": ["true", "false"]})
def test_coverage_import_time_lib():
    import os
    from pathlib import Path

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install
    from tests.coverage.utils import _get_relpath_dict

    cwd_path = os.getcwd()
    include_path = Path(cwd_path + "/tests/coverage/included_path/")

    install(include_paths=[include_path], collect_import_time_coverage=True)

    from tests.coverage.included_path.import_time_callee import called_in_session_import_time

    ModuleCodeCollector.start_coverage()
    called_in_session_import_time()
    ModuleCodeCollector.stop_coverage()

    executable = _get_relpath_dict(cwd_path, ModuleCodeCollector._instance.lines)
    covered = _get_relpath_dict(cwd_path, ModuleCodeCollector._instance._get_covered_lines(include_imported=False))
    covered_with_imports = _get_relpath_dict(
        cwd_path, ModuleCodeCollector._instance._get_covered_lines(include_imported=True)
    )

    expected_executable = {
        "tests/coverage/included_path/import_time_callee.py": {1, 2, 4, 7, 8, 10, 13, 15},
        "tests/coverage/included_path/import_time_lib.py": {1, 3, 6, 7, 8},
        "tests/coverage/included_path/nested_import_time_lib.py": {1, 4, 5, 6},
    }
    expected_covered = {
        "tests/coverage/included_path/import_time_callee.py": {2, 4},
        "tests/coverage/included_path/import_time_lib.py": {1, 3, 6, 7, 8},
        "tests/coverage/included_path/nested_import_time_lib.py": {1, 4},
    }
    expected_covered_with_imports = {
        "tests/coverage/included_path/import_time_callee.py": {1, 2, 4, 7, 13},
        "tests/coverage/included_path/import_time_lib.py": {1, 3, 6, 7, 8},
        "tests/coverage/included_path/nested_import_time_lib.py": {1, 4},
    }

    if os.getenv("_DD_COVERAGE_FILE_LEVEL") == "true":
        # In file-level mode, we only track files, not specific line numbers
        assert executable.keys() == expected_executable.keys(), (
            f"Executable files mismatch: expected={expected_executable.keys()} vs actual={executable.keys()}"
        )
        assert covered.keys() == expected_covered.keys(), (
            f"Covered files mismatch: expected={expected_covered.keys()} vs actual={covered.keys()}"
        )
        assert covered_with_imports.keys() == expected_covered_with_imports.keys(), (
            f"Covered files with imports mismatch: expected={expected_covered_with_imports.keys()}"
            f" vs actual={covered_with_imports.keys()}"
        )
    else:
        # In full coverage mode, we track exact line numbers
        assert executable == expected_executable, (
            f"Executable lines mismatch: expected={expected_executable} vs actual={executable}"
        )
        assert covered == expected_covered, f"Covered lines mismatch: expected={expected_covered} vs actual={covered}"
        assert covered_with_imports == expected_covered_with_imports, (
            f"Covered lines with imports mismatch: expected={expected_covered_with_imports} "
            f"vs actual={covered_with_imports}"
        )


@pytest.mark.subprocess(parametrize={"_DD_COVERAGE_FILE_LEVEL": ["true", "false"]})
def test_coverage_import_time_function():
    import os
    from pathlib import Path

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install
    from tests.coverage.utils import _get_relpath_dict

    cwd_path = os.getcwd()
    include_path = Path(cwd_path + "/tests/coverage/included_path/")

    install(include_paths=[include_path], collect_import_time_coverage=True)

    # The following constant is imported, but not used, so that, by the time it is also imported in
    # calls_function_imported_in_function , it will be only be covered if the include_imported flag
    # is set to True
    from tests.coverage.included_path.imported_in_function_lib import module_level_constant  # noqa

    from tests.coverage.included_path.import_time_callee import calls_function_imported_in_function

    ModuleCodeCollector.start_coverage()
    calls_function_imported_in_function()
    ModuleCodeCollector.stop_coverage()

    lines = _get_relpath_dict(cwd_path, ModuleCodeCollector._instance.lines)
    covered = _get_relpath_dict(cwd_path, ModuleCodeCollector._instance._get_covered_lines(include_imported=False))
    covered_with_imports = _get_relpath_dict(
        cwd_path, ModuleCodeCollector._instance._get_covered_lines(include_imported=True)
    )

    expected_lines = {
        "tests/coverage/included_path/imported_in_function_lib.py": {1, 2, 3, 4, 7},
        "tests/coverage/included_path/import_time_callee.py": {1, 2, 4, 7, 8, 10, 13, 15},
    }
    expected_covered = {"tests/coverage/included_path/import_time_callee.py": {8, 10}}
    expected_covered_with_imports = {
        "tests/coverage/included_path/import_time_callee.py": {1, 7, 8, 10, 13},
        "tests/coverage/included_path/imported_in_function_lib.py": {1, 2, 3, 4, 7},
    }

    if os.getenv("_DD_COVERAGE_FILE_LEVEL") == "true":
        # In file-level mode, we only track files, not specific line numbers
        assert lines.keys() == expected_lines.keys(), (
            f"Executable files mismatch: expected={expected_lines.keys()} vs actual={lines.keys()}"
        )
        assert covered.keys() == expected_covered.keys(), (
            f"Covered files mismatch: expected={expected_covered.keys()} vs actual={covered.keys()}"
        )
        assert covered_with_imports.keys() == expected_covered_with_imports.keys(), (
            f"Covered files with imports mismatch: expected={expected_covered_with_imports.keys()} "
            f"vs actual={covered_with_imports.keys()}"
        )
    else:
        # In full coverage mode, we track exact line numbers
        assert lines == expected_lines, f"Executable lines mismatch: expected={expected_lines} vs actual={lines}"
        assert covered == expected_covered, f"Covered lines mismatch: expected={expected_covered} vs actual={covered}"
        assert covered_with_imports == expected_covered_with_imports, (
            f"Covered lines with imports mismatch: expected={expected_covered_with_imports} "
            f"vs actual={covered_with_imports}"
        )
