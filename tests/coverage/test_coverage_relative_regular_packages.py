import pytest


@pytest.mark.subprocess
def test_coverage_regular_package_import_normal():
    """This test validates that regular packages are correctly covered when imported normally"""
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

    assert (
        executable == expected_executable
    ), f"Executable lines mismatch: expected={expected_executable} vs actual={executable}"
    assert covered == expected_covered, f"Covered lines mismatch: expected={expected_covered} vs actual={covered}"
    assert (
        covered_with_imports == expected_covered_with_imports
    ), f"Covered lines with imports mismatch: expected={expected_covered_with_imports} vs actual={covered_with_imports}"


@pytest.mark.subprocess
def test_coverage_regular_package_import_late():
    """Regular packages are correctly covered when they are imported late"""
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
    from tests.coverage.included_path.imports_rp_dot import imports_rp_dot_late

    ModuleCodeCollector.start_coverage()
    imports_rp_dot_late()
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
        "tests/coverage/included_path/late_import_const.py": {1},
        "tests/coverage/included_path/normal_import_const.py": {1},
        "tests/coverage/included_path/rpa/__init__.py": {_empty_init_lineno},
        "tests/coverage/included_path/rpa/late_import_const.py": {1},
        "tests/coverage/included_path/rpa/normal_import_const.py": {1},
        "tests/coverage/included_path/rpa/rpa/__init__.py": {_empty_init_lineno},
        "tests/coverage/included_path/rpa/rpa/late_import_const.py": {1},
        "tests/coverage/included_path/rpa/rpb/late_import_const.py": {1},
        "tests/coverage/included_path/rpa/rpb/__init__.py": {_empty_init_lineno},
        "tests/coverage/included_path/rpa/rpb/normal_import_const.py": {1},
        "tests/coverage/included_path/rpb/__init__.py": {_empty_init_lineno},
        "tests/coverage/included_path/rpb/late_import_const.py": {1},
        "tests/coverage/included_path/rpb/normal_import_const.py": {1},
    }
    expected_covered = {
        "tests/coverage/included_path/imports_rp_dot.py": {12, 13, 14, 15, 16, 18},
        "tests/coverage/included_path/late_import_const.py": {1},
        "tests/coverage/included_path/rpa/late_import_const.py": {1},
        "tests/coverage/included_path/rpa/rpa/__init__.py": {_empty_init_lineno},
        "tests/coverage/included_path/rpa/rpa/late_import_const.py": {1},
        "tests/coverage/included_path/rpa/rpb/late_import_const.py": {1},
        "tests/coverage/included_path/rpb/late_import_const.py": {1},
    }
    expected_covered_with_imports = {
        "tests/coverage/included_path/imports_rp_dot.py": {1, 2, 3, 4, 7, 11, 12, 13, 14, 15, 16, 18},
        "tests/coverage/included_path/late_import_const.py": {1},
        "tests/coverage/included_path/rpa/late_import_const.py": {1},
        "tests/coverage/included_path/rpa/rpa/__init__.py": {_empty_init_lineno},
        "tests/coverage/included_path/rpa/rpa/late_import_const.py": {1},
        "tests/coverage/included_path/rpa/rpb/late_import_const.py": {1},
        "tests/coverage/included_path/rpb/late_import_const.py": {1},
        "tests/coverage/included_path/rpa/__init__.py": {_empty_init_lineno},
        "tests/coverage/included_path/rpa/rpb/normal_import_const.py": {1},
        "tests/coverage/included_path/rpa/rpb/__init__.py": {_empty_init_lineno},
        "tests/coverage/included_path/rpa/normal_import_const.py": {1},
        "tests/coverage/included_path/rpb/__init__.py": {_empty_init_lineno},
        "tests/coverage/included_path/normal_import_const.py": {1},
        "tests/coverage/included_path/rpb/normal_import_const.py": {1},
    }

    assert (
        executable == expected_executable
    ), f"Executable lines mismatch: expected={expected_executable} vs actual={executable}"
    assert covered == expected_covered, f"Covered lines mismatch: expected={expected_covered} vs actual={covered}"
    assert (
        covered_with_imports == expected_covered_with_imports
    ), f"Covered lines with imports mismatch: expected={expected_covered_with_imports} vs actual={covered_with_imports}"


@pytest.mark.subprocess
def test_coverage_regular_package_rpa_import_parent_normal():
    """Regular packages are correctly covered when imported normally when using a nested package"""
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
    from tests.coverage.included_path.rpa.rpa_imports_parent import rpa_imports_parent_normal

    ModuleCodeCollector.start_coverage()
    rpa_imports_parent_normal()
    ModuleCodeCollector.stop_coverage()

    executable = _get_relpath_dict(cwd_path, ModuleCodeCollector._instance.lines)
    covered = _get_relpath_dict(cwd_path, ModuleCodeCollector._instance._get_covered_lines(include_imported=False))
    covered_with_imports = _get_relpath_dict(
        cwd_path, ModuleCodeCollector._instance._get_covered_lines(include_imported=True)
    )

    # In Python 3.11+, empty __init__.py modules have line 0:
    _empty_init_lineno = 0 if sys.version_info >= (3, 11) else 1

    expected_executable = {
        "tests/coverage/included_path/normal_import_const.py": {1},
        "tests/coverage/included_path/rpa/__init__.py": {_empty_init_lineno},
        "tests/coverage/included_path/rpa/rpa_imports_parent.py": {1, 2, 5, 6, 9, 10, 12},
    }
    expected_covered = {"tests/coverage/included_path/rpa/rpa_imports_parent.py": {6}}
    expected_covered_with_imports = {
        "tests/coverage/included_path/normal_import_const.py": {1},
        "tests/coverage/included_path/rpa/__init__.py": {_empty_init_lineno},
        "tests/coverage/included_path/rpa/rpa_imports_parent.py": {1, 2, 5, 6, 9},
    }

    assert (
        executable == expected_executable
    ), f"Executable lines mismatch: expected={expected_executable} vs actual={executable}"
    assert covered == expected_covered, f"Covered lines mismatch: expected={expected_covered} vs actual={covered}"
    assert (
        covered_with_imports == expected_covered_with_imports
    ), f"Covered lines with imports mismatch: expected={expected_covered_with_imports} vs actual={covered_with_imports}"


@pytest.mark.subprocess
def test_coverage_regular_package_rpa_import_parent_late():
    """Regular packages are correctly covered when imported normally when using a nested package"""
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
    from tests.coverage.included_path.rpa.rpa_imports_parent import rpa_imports_parent_late

    ModuleCodeCollector.start_coverage()
    rpa_imports_parent_late()
    ModuleCodeCollector.stop_coverage()

    executable = _get_relpath_dict(cwd_path, ModuleCodeCollector._instance.lines)
    covered = _get_relpath_dict(cwd_path, ModuleCodeCollector._instance._get_covered_lines(include_imported=False))
    covered_with_imports = _get_relpath_dict(
        cwd_path, ModuleCodeCollector._instance._get_covered_lines(include_imported=True)
    )

    # In Python 3.11+, empty __init__.py modules have line 0:
    _empty_init_lineno = 0 if sys.version_info >= (3, 11) else 1

    expected_executable = {
        "tests/coverage/included_path/late_import_const.py": {1},
        "tests/coverage/included_path/normal_import_const.py": {1},
        "tests/coverage/included_path/rpa/__init__.py": {_empty_init_lineno},
        "tests/coverage/included_path/rpa/rpa_imports_parent.py": {1, 2, 5, 6, 9, 10, 12},
    }
    expected_covered = {
        "tests/coverage/included_path/late_import_const.py": {1},
        "tests/coverage/included_path/rpa/rpa_imports_parent.py": {10, 12},
    }
    expected_covered_with_imports = {
        "tests/coverage/included_path/late_import_const.py": {1},
        "tests/coverage/included_path/normal_import_const.py": {1},
        "tests/coverage/included_path/rpa/__init__.py": {_empty_init_lineno},
        "tests/coverage/included_path/rpa/rpa_imports_parent.py": {1, 2, 5, 9, 10, 12},
    }

    assert (
        executable == expected_executable
    ), f"Executable lines mismatch: expected={expected_executable} vs actual={executable}"
    assert covered == expected_covered, f"Covered lines mismatch: expected={expected_covered} vs actual={covered}"
    assert (
        covered_with_imports == expected_covered_with_imports
    ), f"Covered lines with imports mismatch: expected={expected_covered_with_imports} vs actual={covered_with_imports}"


@pytest.mark.subprocess
def test_coverage_regular_package_rpa_import_dot_normal():
    """Regular packages are correctly covered when imported normally when using a nested package"""
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
    from tests.coverage.included_path.rpa.rpa_imports_dot import rpa_imports_dot_normal

    ModuleCodeCollector.start_coverage()
    rpa_imports_dot_normal()
    ModuleCodeCollector.stop_coverage()

    executable = _get_relpath_dict(cwd_path, ModuleCodeCollector._instance.lines)
    covered = _get_relpath_dict(cwd_path, ModuleCodeCollector._instance._get_covered_lines(include_imported=False))
    covered_with_imports = _get_relpath_dict(
        cwd_path, ModuleCodeCollector._instance._get_covered_lines(include_imported=True)
    )

    # In Python 3.11+, empty __init__.py modules have line 0:
    _empty_init_lineno = 0 if sys.version_info >= (3, 11) else 1

    expected_executable = {
        "tests/coverage/included_path/rpa/__init__.py": {_empty_init_lineno},
        "tests/coverage/included_path/rpa/normal_import_const.py": {1},
        "tests/coverage/included_path/rpa/rpa/__init__.py": {_empty_init_lineno},
        "tests/coverage/included_path/rpa/rpa/normal_import_const.py": {1},
        "tests/coverage/included_path/rpa/rpa_imports_dot.py": {1, 2, 3, 6, 7, 10, 11, 12, 13, 15},
        "tests/coverage/included_path/rpa/rpb/__init__.py": {_empty_init_lineno},
        "tests/coverage/included_path/rpa/rpb/normal_import_const.py": {1},
    }
    expected_covered = {"tests/coverage/included_path/rpa/rpa_imports_dot.py": {7}}
    expected_covered_with_imports = {
        "tests/coverage/included_path/rpa/__init__.py": {_empty_init_lineno},
        "tests/coverage/included_path/rpa/normal_import_const.py": {1},
        "tests/coverage/included_path/rpa/rpa/__init__.py": {_empty_init_lineno},
        "tests/coverage/included_path/rpa/rpa/normal_import_const.py": {1},
        "tests/coverage/included_path/rpa/rpa_imports_dot.py": {1, 2, 3, 6, 7, 10},
        "tests/coverage/included_path/rpa/rpb/__init__.py": {_empty_init_lineno},
        "tests/coverage/included_path/rpa/rpb/normal_import_const.py": {1},
    }

    assert (
        executable == expected_executable
    ), f"Executable lines mismatch: expected={expected_executable} vs actual={executable}"
    assert covered == expected_covered, f"Covered lines mismatch: expected={expected_covered} vs actual={covered}"
    assert (
        covered_with_imports == expected_covered_with_imports
    ), f"Covered lines with imports mismatch: expected={expected_covered_with_imports} vs actual={covered_with_imports}"


@pytest.mark.subprocess
def test_coverage_regular_package_rpa_import_dot_late():
    """Regular packages are correctly covered when imported normally when using a nested package"""
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
    from tests.coverage.included_path.rpa.rpa_imports_dot import rpa_imports_dot_late

    ModuleCodeCollector.start_coverage()
    rpa_imports_dot_late()
    ModuleCodeCollector.stop_coverage()

    executable = _get_relpath_dict(cwd_path, ModuleCodeCollector._instance.lines)
    covered = _get_relpath_dict(cwd_path, ModuleCodeCollector._instance._get_covered_lines(include_imported=False))
    covered_with_imports = _get_relpath_dict(
        cwd_path, ModuleCodeCollector._instance._get_covered_lines(include_imported=True)
    )

    # In Python 3.11+, empty __init__.py modules have line 0:
    _empty_init_lineno = 0 if sys.version_info >= (3, 11) else 1

    expected_executable = {
        "tests/coverage/included_path/rpa/__init__.py": {_empty_init_lineno},
        "tests/coverage/included_path/rpa/late_import_const.py": {1},
        "tests/coverage/included_path/rpa/normal_import_const.py": {1},
        "tests/coverage/included_path/rpa/rpa/__init__.py": {_empty_init_lineno},
        "tests/coverage/included_path/rpa/rpa/late_import_const.py": {1},
        "tests/coverage/included_path/rpa/rpa/normal_import_const.py": {1},
        "tests/coverage/included_path/rpa/rpa_imports_dot.py": {1, 2, 3, 6, 7, 10, 11, 12, 13, 15},
        "tests/coverage/included_path/rpa/rpb/__init__.py": {_empty_init_lineno},
        "tests/coverage/included_path/rpa/rpb/late_import_const.py": {1},
        "tests/coverage/included_path/rpa/rpb/normal_import_const.py": {1},
    }

    expected_covered = {
        "tests/coverage/included_path/rpa/rpa_imports_dot.py": {11, 12, 13, 15},
        "tests/coverage/included_path/rpa/late_import_const.py": {1},
        "tests/coverage/included_path/rpa/rpa/late_import_const.py": {1},
        "tests/coverage/included_path/rpa/rpb/late_import_const.py": {1},
    }
    expected_covered_with_imports = {
        "tests/coverage/included_path/rpa/__init__.py": {_empty_init_lineno},
        "tests/coverage/included_path/rpa/late_import_const.py": {1},
        "tests/coverage/included_path/rpa/normal_import_const.py": {1},
        "tests/coverage/included_path/rpa/rpa/__init__.py": {_empty_init_lineno},
        "tests/coverage/included_path/rpa/rpa/late_import_const.py": {1},
        "tests/coverage/included_path/rpa/rpa/normal_import_const.py": {1},
        "tests/coverage/included_path/rpa/rpa_imports_dot.py": {1, 2, 3, 6, 10, 11, 12, 13, 15},
        "tests/coverage/included_path/rpa/rpb/__init__.py": {_empty_init_lineno},
        "tests/coverage/included_path/rpa/rpb/late_import_const.py": {1},
        "tests/coverage/included_path/rpa/rpb/normal_import_const.py": {1},
    }

    assert (
        executable == expected_executable
    ), f"Executable lines mismatch: expected={expected_executable} vs actual={executable}"
    assert covered == expected_covered, f"Covered lines mismatch: expected={expected_covered} vs actual={covered}"
    assert (
        covered_with_imports == expected_covered_with_imports
    ), f"Covered lines with imports mismatch: expected={expected_covered_with_imports} vs actual={covered_with_imports}"
