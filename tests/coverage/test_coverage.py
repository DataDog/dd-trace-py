"""This file includes various tests that exercise the internal coverage collection module

Tests use the subprocess pytest mark to ensure that coverage collection happens in a clean environment.

Tests that cover import-time dependencies are meant to catch issues (important to the Intelligent Test Runner) with
lines that code technically depends on (eg: imported functions, classes, or constants), but are executed at import
time rather than at code execution time.
"""

import pytest


@pytest.mark.subprocess
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

    assert (
        executable == expected_executable
    ), f"Executable lines mismatch: expected={expected_executable} vs actual={executable}"
    assert covered == expected_covered, f"Covered lines mismatch: expected={expected_covered} vs actual={covered}"
    assert (
        covered_with_imports == expected_covered_with_imports
    ), f"Covered lines with imports mismatch: expected={expected_covered_with_imports} vs actual={covered_with_imports}"


@pytest.mark.subprocess
def test_coverage_namespace_package_import_normal():
    """This test validates that namespace packages are correctly covered when imported normally"""
    import os
    from pathlib import Path

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install
    from tests.coverage.utils import _get_relpath_dict

    cwd_path = os.getcwd()
    include_path = Path(cwd_path + "/tests/coverage/included_path/")

    install(include_paths=[include_path], collect_import_time_coverage=True)

    # Functions are done prior to importing coverage so test that import-time dependencies are covered
    from tests.coverage.included_path.imports_ns_dot import imports_ns_dot_normal

    ModuleCodeCollector.start_coverage()
    imports_ns_dot_normal()
    ModuleCodeCollector.stop_coverage()

    executable = _get_relpath_dict(cwd_path, ModuleCodeCollector._instance.lines)
    covered = _get_relpath_dict(cwd_path, ModuleCodeCollector._instance._get_covered_lines(include_imported=False))
    covered_with_imports = _get_relpath_dict(
        cwd_path, ModuleCodeCollector._instance._get_covered_lines(include_imported=True)
    )

    expected_executable = {
        "tests/coverage/included_path/imports_ns_dot.py": {1, 2, 3, 4, 7, 8, 11, 12, 13, 14, 15, 16, 18},
        "tests/coverage/included_path/normal_import_const.py": {1},
        "tests/coverage/included_path/nsa/normal_import_const.py": {1},
        "tests/coverage/included_path/nsa/nsb/normal_import_const.py": {1},
        "tests/coverage/included_path/nsb/normal_import_const.py": {1},
    }
    expected_covered = {
        "tests/coverage/included_path/imports_ns_dot.py": {8},
    }
    expected_covered_with_imports = {
        "tests/coverage/included_path/imports_ns_dot.py": {1, 2, 3, 4, 7, 8, 11},
        "tests/coverage/included_path/normal_import_const.py": {1},
        "tests/coverage/included_path/nsa/normal_import_const.py": {1},
        "tests/coverage/included_path/nsa/nsb/normal_import_const.py": {1},
        "tests/coverage/included_path/nsb/normal_import_const.py": {1},
    }

    from pprint import pprint

    print("\n\nExecutable lines:")
    pprint(executable)
    print("\n\nExpected executable:")
    pprint(expected_executable)
    print("\n\nImport time coverage:")
    pprint(ModuleCodeCollector._instance._import_time_covered)
    print("\n\nImport time names:")
    pprint(ModuleCodeCollector._instance._import_time_name_to_path)
    print("\n\nImport names by path:")
    pprint(ModuleCodeCollector._instance._import_names_by_path)
    print("\n\nExpected imports")
    pprint(expected_covered_with_imports)
    print("\n\nActual imports")
    pprint(covered_with_imports)
    print("\n\n")

    assert (
        executable == expected_executable
    ), f"Executable lines mismatch: expected={expected_executable} vs actual={executable}"
    assert covered == expected_covered, f"Covered lines mismatch: expected={expected_covered} vs actual={covered}"
    assert (
        covered_with_imports == expected_covered_with_imports
    ), f"Covered lines with imports mismatch: expected={expected_covered_with_imports} vs actual={covered_with_imports}"


@pytest.mark.subprocess
def test_coverage_namespace_package_import_late():
    """This test validates that namespace packages are correctly covered when they are imported late"""
    import os
    from pathlib import Path

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install
    from tests.coverage.utils import _get_relpath_dict

    cwd_path = os.getcwd()
    include_path = Path(cwd_path + "/tests/coverage/included_path/")

    install(include_paths=[include_path], collect_import_time_coverage=True)

    # Functions are done prior to importing coverage so test that import-time dependencies are covered
    from tests.coverage.included_path.imports_ns_dot import imports_ns_dot_late

    ModuleCodeCollector.start_coverage()
    imports_ns_dot_late()
    ModuleCodeCollector.stop_coverage()

    executable = _get_relpath_dict(cwd_path, ModuleCodeCollector._instance.lines)
    covered = _get_relpath_dict(cwd_path, ModuleCodeCollector._instance._get_covered_lines(include_imported=False))
    covered_with_imports = _get_relpath_dict(
        cwd_path, ModuleCodeCollector._instance._get_covered_lines(include_imported=True)
    )

    expected_executable = {
        "tests/coverage/included_path/imports_ns_dot.py": {1, 2, 3, 4, 7, 8, 11, 12, 13, 14, 15, 16, 18},
        "tests/coverage/included_path/late_import_const.py": {1},
        "tests/coverage/included_path/normal_import_const.py": {1},
        "tests/coverage/included_path/nsa/late_import_const.py": {1},
        "tests/coverage/included_path/nsa/normal_import_const.py": {1},
        "tests/coverage/included_path/nsa/nsa/late_import_const.py": {1},
        "tests/coverage/included_path/nsa/nsb/late_import_const.py": {1},
        "tests/coverage/included_path/nsa/nsb/normal_import_const.py": {1},
        "tests/coverage/included_path/nsb/late_import_const.py": {1},
        "tests/coverage/included_path/nsb/normal_import_const.py": {1},
    }
    expected_covered = {
        "tests/coverage/included_path/imports_ns_dot.py": {12, 13, 14, 15, 16, 18},
        "tests/coverage/included_path/late_import_const.py": {1},
        "tests/coverage/included_path/nsa/late_import_const.py": {1},
        "tests/coverage/included_path/nsa/nsa/late_import_const.py": {1},
        "tests/coverage/included_path/nsa/nsb/late_import_const.py": {1},
        "tests/coverage/included_path/nsb/late_import_const.py": {1},
    }
    expected_covered_with_imports = {
        "tests/coverage/included_path/imports_ns_dot.py": {1, 2, 3, 4, 7, 11, 12, 13, 14, 15, 16, 18},
        "tests/coverage/included_path/late_import_const.py": {1},
        "tests/coverage/included_path/normal_import_const.py": {1},
        "tests/coverage/included_path/nsa/late_import_const.py": {1},
        "tests/coverage/included_path/nsa/normal_import_const.py": {1},
        "tests/coverage/included_path/nsa/nsa/late_import_const.py": {1},
        "tests/coverage/included_path/nsa/nsb/late_import_const.py": {1},
        "tests/coverage/included_path/nsa/nsb/normal_import_const.py": {1},
        "tests/coverage/included_path/nsb/late_import_const.py": {1},
        "tests/coverage/included_path/nsb/normal_import_const.py": {1},
    }

    from pprint import pprint

    print("\n\nExecutable lines:")
    pprint(executable)
    print("\n\nExpected executable:")
    pprint(expected_executable)
    print("\n\nImport time coverage:")
    pprint(ModuleCodeCollector._instance._import_time_covered)
    print("\n\nImport time names:")
    pprint(ModuleCodeCollector._instance._import_time_name_to_path)
    print("\n\nImport names by path:")
    pprint(ModuleCodeCollector._instance._import_names_by_path)
    print("\n\nExpected imports")
    pprint(expected_covered_with_imports)
    print("\n\nActual imports")
    pprint(covered_with_imports)
    print("\n\n")

    assert (
        executable == expected_executable
    ), f"Executable lines mismatch: expected={expected_executable} vs actual={executable}"
    assert covered == expected_covered, f"Covered lines mismatch: expected={expected_covered} vs actual={covered}"
    assert (
        covered_with_imports == expected_covered_with_imports
    ), f"Covered lines with imports mismatch: expected={expected_covered_with_imports} vs actual={covered_with_imports}"


@pytest.mark.subprocess
def test_coverage_regular_package_import_normal():
    """This test validates that namespace packages are correctly covered when imported normally"""
    import os
    from pathlib import Path
    import sys

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install
    from tests.coverage.utils import _get_relpath_dict

    cwd_path = os.getcwd()
    include_path = Path(cwd_path + "/tests/coverage/included_path/")

    install(include_paths=[include_path], collect_import_time_coverage=True)

    # Functions are done prior to importing coverage so test that import-time dependencies are covered
    from tests.coverage.included_path.imports_rp_dot import imports_rp_dot_normal

    ModuleCodeCollector.start_coverage()
    imports_rp_dot_normal()
    ModuleCodeCollector.stop_coverage()

    executable = _get_relpath_dict(cwd_path, ModuleCodeCollector._instance.lines)
    covered = _get_relpath_dict(cwd_path, ModuleCodeCollector._instance._get_covered_lines(include_imported=False))
    covered_with_imports = _get_relpath_dict(
        cwd_path, ModuleCodeCollector._instance._get_covered_lines(include_imported=True)
    )

    # In Python 3.11+, empty __init__.py modules have line 0:
    _empty_init_lineno = 0 if sys.version_info >= (3, 11) else 1

    expected_executable = {
        "tests/coverage/included_path/imports_rp_dot.py": {1, 2, 3, 4, 7, 8, 11, 12, 13, 14, 15, 16, 18},
        "tests/coverage/included_path/normal_import_const.py": {1},
        "tests/coverage/included_path/rpa/__init__.py": {_empty_init_lineno},
        "tests/coverage/included_path/rpa/normal_import_const.py": {1},
        "tests/coverage/included_path/rpa/rpb/__init__.py": {_empty_init_lineno},
        "tests/coverage/included_path/rpa/rpb/normal_import_const.py": {1},
        "tests/coverage/included_path/rpb/__init__.py": {_empty_init_lineno},
        "tests/coverage/included_path/rpb/normal_import_const.py": {1},
    }
    expected_covered = {
        "tests/coverage/included_path/imports_rp_dot.py": {8},
    }
    expected_covered_with_imports = {
        "tests/coverage/included_path/imports_rp_dot.py": {1, 2, 3, 4, 7, 8, 11},
        "tests/coverage/included_path/normal_import_const.py": {1},
        "tests/coverage/included_path/rpa/__init__.py": {_empty_init_lineno},
        "tests/coverage/included_path/rpa/normal_import_const.py": {1},
        "tests/coverage/included_path/rpa/rpb/__init__.py": {_empty_init_lineno},
        "tests/coverage/included_path/rpa/rpb/normal_import_const.py": {1},
        "tests/coverage/included_path/rpb/__init__.py": {_empty_init_lineno},
        "tests/coverage/included_path/rpb/normal_import_const.py": {1},
    }

    from pprint import pprint

    print("\n\nExecutable lines:")
    pprint(executable)
    print("\n\nExpected executable:")
    pprint(expected_executable)
    print("\n\nImport time coverage:")
    pprint(ModuleCodeCollector._instance._import_time_covered)
    print("\n\nImport time names:")
    pprint(ModuleCodeCollector._instance._import_time_name_to_path)
    print("\n\nImport names by path:")
    pprint(ModuleCodeCollector._instance._import_names_by_path)
    print("\n\nExpected imports")
    pprint(expected_covered_with_imports)
    print("\n\nActual imports")
    pprint(covered_with_imports)
    print("\n\n")

    assert (
        executable == expected_executable
    ), f"Executable lines mismatch: expected={expected_executable} vs actual={executable}"
    assert covered == expected_covered, f"Covered lines mismatch: expected={expected_covered} vs actual={covered}"
    assert (
        covered_with_imports == expected_covered_with_imports
    ), f"Covered lines with imports mismatch: expected={expected_covered_with_imports} vs actual={covered_with_imports}"


@pytest.mark.subprocess
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

    assert lines == expected_lines, f"Executable lines mismatch: expected={expected_lines} vs actual={lines}"
    assert covered == expected_covered, f"Covered lines mismatch: expected={expected_covered} vs actual={covered}"
    assert (
        covered_with_imports == expected_covered_with_imports
    ), f"Covered lines with imports mismatch: expected={expected_covered_with_imports} vs actual={covered_with_imports}"
