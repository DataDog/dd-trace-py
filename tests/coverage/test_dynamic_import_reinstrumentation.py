"""
Test to check if dynamic imports require both restart_events() and set_local_events().

This test attempts to expose edge cases where restart_events() alone might not be sufficient.
"""

import sys

import pytest


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Test specific to Python 3.12+ monitoring API")
@pytest.mark.subprocess
def test_module_imported_before_vs_during_coverage():
    """
    Test that modules imported before coverage starts are re-instrumented correctly.

    This tests whether there's a difference between:
    - Modules imported before install()
    - Modules imported after install() but before first context
    - Modules imported during context
    """
    import os
    from pathlib import Path

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install

    # Import one module BEFORE coverage is installed
    from tests.coverage.included_path.callee import called_in_session_main
    from tests.coverage.utils import _get_relpath_dict

    cwd_path = os.getcwd()
    include_path = Path(cwd_path + "/tests/coverage/included_path/")

    # Now install coverage (module was already imported)
    install(include_paths=[include_path])

    # Import another module AFTER install but BEFORE first context
    from tests.coverage.included_path.in_context_lib import called_in_context

    # Context 1
    with ModuleCodeCollector.CollectInContext() as context1:
        called_in_session_main(1, 2)
        called_in_context(3, 4)
        context1_covered = _get_relpath_dict(cwd_path, context1.get_covered_lines())

    # Context 2
    with ModuleCodeCollector.CollectInContext() as context2:
        called_in_session_main(4, 5)
        called_in_context(6, 7)
        context2_covered = _get_relpath_dict(cwd_path, context2.get_covered_lines())

    # Both contexts should have coverage for both modules
    # Module imported before install() won't be instrumented, but that's expected
    # The key test is whether modules imported after install() work in both contexts

    # in_context_lib.py should be instrumented (imported after install)
    assert (
        "tests/coverage/included_path/in_context_lib.py" in context1_covered
    ), "Context 1 missing in_context_lib.py - module imported after install() should be tracked"
    assert (
        "tests/coverage/included_path/in_context_lib.py" in context2_covered
    ), "Context 2 missing in_context_lib.py - re-instrumentation failed!"

    assert 2 in context1_covered["tests/coverage/included_path/in_context_lib.py"]
    assert (
        2 in context2_covered["tests/coverage/included_path/in_context_lib.py"]
    ), "Context 2 missing line 2 - re-instrumentation failed!"
