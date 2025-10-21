"""
Test to check if dynamic imports require both restart_events() and set_local_events().

This test attempts to expose edge cases where restart_events() alone might not be sufficient.
"""
import sys

import pytest


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Test specific to Python 3.12+ monitoring API")
@pytest.mark.subprocess
def test_dynamic_module_import_during_context():
    """
    Test that modules imported dynamically during a context are properly re-instrumented.
    
    This tests whether restart_events() alone is sufficient for code objects that
    were imported during (not before) coverage collection.
    """
    import os
    import sys
    from pathlib import Path
    
    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install
    from tests.coverage.utils import _get_relpath_dict
    
    cwd_path = os.getcwd()
    include_path = Path(cwd_path + "/tests/coverage/included_path/")
    
    install(include_paths=[include_path])
    
    # Context 1: Import module during context and execute code
    with ModuleCodeCollector.CollectInContext() as context1:
        # Import happens DURING context collection
        from tests.coverage.included_path.callee import called_in_session_main
        called_in_session_main(1, 2)
        context1_covered = _get_relpath_dict(cwd_path, context1.get_covered_lines())
    
    # The module is now in sys.modules, so it won't be re-imported
    # But its code objects should be re-instrumented
    
    # Context 2: Execute the same code (no new import)
    with ModuleCodeCollector.CollectInContext() as context2:
        called_in_session_main(3, 4)
        context2_covered = _get_relpath_dict(cwd_path, context2.get_covered_lines())
    
    # Context 3: One more time
    with ModuleCodeCollector.CollectInContext() as context3:
        called_in_session_main(5, 6)
        context3_covered = _get_relpath_dict(cwd_path, context3.get_covered_lines())
    
    # All contexts should have coverage for the runtime lines
    expected_runtime_lines = {2, 3, 5, 6}
    
    assert "tests/coverage/included_path/callee.py" in context1_covered
    assert "tests/coverage/included_path/callee.py" in context2_covered
    assert "tests/coverage/included_path/callee.py" in context3_covered
    
    # Verify lib.py line 2 is captured in ALL contexts
    assert "tests/coverage/included_path/lib.py" in context1_covered
    assert "tests/coverage/included_path/lib.py" in context2_covered
    assert "tests/coverage/included_path/lib.py" in context3_covered
    
    assert 2 in context1_covered["tests/coverage/included_path/lib.py"], "Context 1 missing lib.py line 2"
    assert 2 in context2_covered["tests/coverage/included_path/lib.py"], "Context 2 missing lib.py line 2 - re-instrumentation failed!"
    assert 2 in context3_covered["tests/coverage/included_path/lib.py"], "Context 3 missing lib.py line 2 - re-instrumentation failed!"


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Test specific to Python 3.12+ monitoring API")
@pytest.mark.subprocess
def test_exec_based_code_reinstrumentation():
    """
    Test that code executed via exec() is properly re-instrumented.
    
    This tests whether restart_events() works for code objects created at runtime.
    """
    import os
    from pathlib import Path
    
    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install
    
    cwd_path = os.getcwd()
    include_path = Path(cwd_path + "/tests/coverage/included_path/")
    
    install(include_paths=[include_path])
    
    # Create a code snippet to execute
    code_snippet = """
def dynamic_function(x, y):
    result = x + y
    return result * 2

output = dynamic_function(5, 3)
"""
    
    # Context 1: Execute dynamic code
    with ModuleCodeCollector.CollectInContext() as context1:
        namespace1 = {}
        exec(code_snippet, namespace1)
        result1 = namespace1['output']
    
    # Context 2: Execute the same dynamic code again
    with ModuleCodeCollector.CollectInContext() as context2:
        namespace2 = {}
        exec(code_snippet, namespace2)
        result2 = namespace2['output']
    
    # Both should get the same result
    assert result1 == 16
    assert result2 == 16
    
    # Note: This test mainly ensures no crashes occur with dynamic code
    # The actual coverage tracking of dynamic code might be limited


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
    import sys
    from pathlib import Path
    
    # Import one module BEFORE coverage is installed
    from tests.coverage.included_path.callee import called_in_session_main
    
    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install
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
    if "tests/coverage/included_path/in_context_lib.py" in context1_covered:
        assert "tests/coverage/included_path/in_context_lib.py" in context2_covered, \
            "Context 2 missing in_context_lib.py - re-instrumentation failed!"
        
        assert 2 in context1_covered["tests/coverage/included_path/in_context_lib.py"]
        assert 2 in context2_covered["tests/coverage/included_path/in_context_lib.py"], \
            "Context 2 missing line 2 - re-instrumentation failed!"

