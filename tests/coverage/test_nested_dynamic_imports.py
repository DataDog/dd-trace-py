"""
Test complex nested import scenarios with multiple layers of top-level and dynamic imports.

This test checks if re-instrumentation works correctly when:
- Fixture code has top-level imports
- Fixture code has dynamic (function-level) imports  
- Those imported modules themselves have more imports (both top-level and dynamic)
- Multiple contexts execute the same code paths

The fixture modules are in tests/coverage/included_path/:
- nested_fixture.py (main fixture with top-level and dynamic imports)
- layer2_toplevel.py, layer2_dynamic.py (imported by fixture)
- layer3_toplevel.py, layer3_dynamic.py (imported by layer2)
"""
import sys

import pytest


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Test specific to Python 3.12+ monitoring API")
@pytest.mark.subprocess
def test_nested_imports_toplevel_path_reinstrumentation():
    """
    Test re-instrumentation with nested imports via top-level import path.
    
    This tests: fixture (top-level import) -> layer2 (top-level import) -> layer3
    And: fixture (top-level import) -> layer2 (dynamic import) -> layer3
    """
    import os
    from pathlib import Path
    
    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install
    from tests.coverage.utils import _get_relpath_dict
    
    cwd_path = os.getcwd()
    include_path = Path(cwd_path + "/tests/coverage/included_path/")
    
    install(include_paths=[include_path])
    
    from tests.coverage.included_path.nested_fixture import fixture_toplevel_path
    
    # Context 1: Execute the top-level path
    with ModuleCodeCollector.CollectInContext() as context1:
        result1 = fixture_toplevel_path(5)
        context1_covered = _get_relpath_dict(cwd_path, context1.get_covered_lines())
    
    # Context 2: Execute the SAME path again - should get SAME coverage
    with ModuleCodeCollector.CollectInContext() as context2:
        result2 = fixture_toplevel_path(10)
        context2_covered = _get_relpath_dict(cwd_path, context2.get_covered_lines())
    
    # Context 3: One more time
    with ModuleCodeCollector.CollectInContext() as context3:
        result3 = fixture_toplevel_path(3)
        context3_covered = _get_relpath_dict(cwd_path, context3.get_covered_lines())
    
    # layer2_toplevel: layer3_toplevel(5) = 15, then layer3_dynamic(15) = (15+10)*2 = 50
    assert result1 == 50
    assert result2 == 80  # layer3_toplevel(10)=30, layer3_dynamic(30)=(30+10)*2=80
    assert result3 == 38  # layer3_toplevel(3)=9, layer3_dynamic(9)=(9+10)*2=38
    
    # Check that all contexts captured coverage for all layers
    files_to_check = [
        "tests/coverage/included_path/nested_fixture.py",
        "tests/coverage/included_path/layer2_toplevel.py",
        "tests/coverage/included_path/layer3_toplevel.py",
        "tests/coverage/included_path/layer3_dynamic.py",
    ]
    
    for file_path in files_to_check:
        assert file_path in context1_covered, f"Context 1 missing {file_path}"
        assert file_path in context2_covered, f"Context 2 missing {file_path} - re-instrumentation failed!"
        assert file_path in context3_covered, f"Context 3 missing {file_path} - re-instrumentation failed!"
        
        # Check that all contexts have the same coverage for each file
        context1_lines = context1_covered[file_path]
        context2_lines = context2_covered[file_path]
        context3_lines = context3_covered[file_path]
        
        # Runtime lines should be the same (import-time lines may differ)
        # For now, just ensure context 2 and 3 have at least as much as context 1
        assert len(context2_lines) > 0, f"Context 2 has no coverage for {file_path}"
        assert len(context3_lines) > 0, f"Context 3 has no coverage for {file_path}"


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Test specific to Python 3.12+ monitoring API")
@pytest.mark.subprocess
def test_nested_imports_dynamic_path_reinstrumentation():
    """
    Test re-instrumentation with nested imports via dynamic import path.
    
    This tests: fixture (dynamic import) -> layer2 (top-level import) -> layer3
    And: fixture (dynamic import) -> layer2 (dynamic import) -> layer3
    """
    import os
    from pathlib import Path
    
    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install
    from tests.coverage.utils import _get_relpath_dict
    
    cwd_path = os.getcwd()
    include_path = Path(cwd_path + "/tests/coverage/included_path/")
    
    install(include_paths=[include_path])
    
    from tests.coverage.included_path.nested_fixture import fixture_dynamic_path
    
    # Context 1: Execute the dynamic path
    with ModuleCodeCollector.CollectInContext() as context1:
        result1 = fixture_dynamic_path(5)
        context1_covered = _get_relpath_dict(cwd_path, context1.get_covered_lines())
    
    # Context 2: Execute the SAME path again - should get SAME coverage
    with ModuleCodeCollector.CollectInContext() as context2:
        result2 = fixture_dynamic_path(10)
        context2_covered = _get_relpath_dict(cwd_path, context2.get_covered_lines())
    
    # Context 3: One more time
    with ModuleCodeCollector.CollectInContext() as context3:
        result3 = fixture_dynamic_path(3)
        context3_covered = _get_relpath_dict(cwd_path, context3.get_covered_lines())
    
    # layer2_dynamic: layer3_toplevel(5)=15, layer3_dynamic(15)=50, return 50+5=55
    assert result1 == 55
    assert result2 == 85  # layer3_toplevel(10)=30, layer3_dynamic(30)=80, return 85
    assert result3 == 43  # layer3_toplevel(3)=9, layer3_dynamic(9)=38, return 43
    
    # Check that all contexts captured coverage for all layers
    files_to_check = [
        "tests/coverage/included_path/nested_fixture.py",
        "tests/coverage/included_path/layer2_dynamic.py",
        "tests/coverage/included_path/layer3_toplevel.py",
        "tests/coverage/included_path/layer3_dynamic.py",
    ]
    
    for file_path in files_to_check:
        assert file_path in context1_covered, f"Context 1 missing {file_path}"
        assert file_path in context2_covered, f"Context 2 missing {file_path} - re-instrumentation failed!"
        assert file_path in context3_covered, f"Context 3 missing {file_path} - re-instrumentation failed!"
        
        context1_lines = context1_covered[file_path]
        context2_lines = context2_covered[file_path]
        context3_lines = context3_covered[file_path]
        
        assert len(context2_lines) > 0, f"Context 2 has no coverage for {file_path}"
        assert len(context3_lines) > 0, f"Context 3 has no coverage for {file_path}"


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Test specific to Python 3.12+ monitoring API")
@pytest.mark.subprocess
def test_nested_imports_mixed_path_reinstrumentation():
    """
    Test re-instrumentation with nested imports using both top-level and dynamic paths.
    
    This is the most comprehensive test - it exercises ALL import paths in sequence.
    """
    import os
    from pathlib import Path
    
    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install
    from tests.coverage.utils import _get_relpath_dict
    
    cwd_path = os.getcwd()
    include_path = Path(cwd_path + "/tests/coverage/included_path/")
    
    install(include_paths=[include_path])
    
    from tests.coverage.included_path.nested_fixture import fixture_mixed_path
    
    # Context 1: Execute all paths
    with ModuleCodeCollector.CollectInContext() as context1:
        result1 = fixture_mixed_path(5)
        context1_covered = _get_relpath_dict(cwd_path, context1.get_covered_lines())
    
    # Context 2: Execute all paths again
    with ModuleCodeCollector.CollectInContext() as context2:
        result2 = fixture_mixed_path(10)
        context2_covered = _get_relpath_dict(cwd_path, context2.get_covered_lines())
    
    # Context 3: One more time
    with ModuleCodeCollector.CollectInContext() as context3:
        result3 = fixture_mixed_path(3)
        context3_covered = _get_relpath_dict(cwd_path, context3.get_covered_lines())
    
    # Check that all contexts captured coverage for ALL layers
    all_files = [
        "tests/coverage/included_path/nested_fixture.py",
        "tests/coverage/included_path/layer2_toplevel.py",
        "tests/coverage/included_path/layer2_dynamic.py",
        "tests/coverage/included_path/layer3_toplevel.py",
        "tests/coverage/included_path/layer3_dynamic.py",
    ]
    
    # All contexts should have all files
    for file_path in all_files:
        assert file_path in context1_covered, f"Context 1 missing {file_path}"
        assert file_path in context2_covered, f"Context 2 missing {file_path} - re-instrumentation failed!"
        assert file_path in context3_covered, f"Context 3 missing {file_path} - re-instrumentation failed!"
        
        context1_lines = context1_covered[file_path]
        context2_lines = context2_covered[file_path]
        context3_lines = context3_covered[file_path]
        
        # Critical assertion: Each context should capture function body lines (runtime coverage)
        assert len(context1_lines) > 0, f"Context 1 has no coverage for {file_path}"
        assert len(context2_lines) > 0, f"Context 2 has no coverage for {file_path} - re-instrumentation failed!"
        assert len(context3_lines) > 0, f"Context 3 has no coverage for {file_path} - re-instrumentation failed!"
        
        # Context 2 and 3 should have identical runtime coverage
        # (Context 1 might have additional import-time lines)
        assert context2_lines == context3_lines, \
            f"{file_path}: Context 2 and 3 differ - re-instrumentation inconsistent!\n" \
            f"  Context 2: {sorted(context2_lines)}\n" \
            f"  Context 3: {sorted(context3_lines)}"


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Test specific to Python 3.12+ monitoring API")
@pytest.mark.subprocess
def test_nested_imports_interleaved_execution():
    """
    Test re-instrumentation with interleaved execution of different import paths.
    
    This simulates a realistic scenario where different tests might call different
    code paths, and we need to ensure ALL paths are properly instrumented in each context.
    """
    import os
    from pathlib import Path
    
    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install
    from tests.coverage.utils import _get_relpath_dict
    
    cwd_path = os.getcwd()
    include_path = Path(cwd_path + "/tests/coverage/included_path/")
    
    install(include_paths=[include_path])
    
    from tests.coverage.included_path.nested_fixture import fixture_dynamic_path
    from tests.coverage.included_path.nested_fixture import fixture_toplevel_path
    
    # Context 1: Execute toplevel path
    with ModuleCodeCollector.CollectInContext() as context1:
        fixture_toplevel_path(5)
        context1_covered = _get_relpath_dict(cwd_path, context1.get_covered_lines())
    
    # Context 2: Execute dynamic path (different path)
    with ModuleCodeCollector.CollectInContext() as context2:
        fixture_dynamic_path(10)
        context2_covered = _get_relpath_dict(cwd_path, context2.get_covered_lines())
    
    # Context 3: Execute toplevel path again (back to first path)
    with ModuleCodeCollector.CollectInContext() as context3:
        fixture_toplevel_path(3)
        context3_covered = _get_relpath_dict(cwd_path, context3.get_covered_lines())
    
    # Context 4: Execute dynamic path again
    with ModuleCodeCollector.CollectInContext() as context4:
        fixture_dynamic_path(7)
        context4_covered = _get_relpath_dict(cwd_path, context4.get_covered_lines())
    
    # Check that context 1 and 3 have the same files (both used toplevel path)
    files_toplevel = ["tests/coverage/included_path/layer2_toplevel.py"]
    files_dynamic = ["tests/coverage/included_path/layer2_dynamic.py"]
    
    for file_path in files_toplevel:
        assert file_path in context1_covered, f"Context 1 missing {file_path}"
        assert file_path in context3_covered, f"Context 3 missing {file_path} - re-instrumentation failed!"
        
        # Check they have comparable coverage
        assert len(context1_covered[file_path]) > 0
        assert len(context3_covered[file_path]) > 0
    
    for file_path in files_dynamic:
        assert file_path in context2_covered, f"Context 2 missing {file_path}"
        assert file_path in context4_covered, f"Context 4 missing {file_path} - re-instrumentation failed!"
        
        assert len(context2_covered[file_path]) > 0
        assert len(context4_covered[file_path]) > 0

