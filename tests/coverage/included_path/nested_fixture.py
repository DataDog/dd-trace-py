"""
Fixture code with complex nested imports.

This fixture has:
- Top-level imports
- Dynamic (function-level) imports
And the imported modules themselves have more imports (both top-level and dynamic)
"""

# Top-level imports
from tests.coverage.included_path.layer2_toplevel import layer2_toplevel_function


def fixture_toplevel_path(value):
    """Uses top-level imported function"""
    result = layer2_toplevel_function(value)
    return result


def fixture_dynamic_path(value):
    """Uses dynamically imported function"""
    # Dynamic import at function level
    from tests.coverage.included_path.layer2_dynamic import layer2_dynamic_function

    result = layer2_dynamic_function(value)
    return result


def fixture_mixed_path(value):
    """Uses both paths"""
    result1 = fixture_toplevel_path(value)
    result2 = fixture_dynamic_path(value)
    return result1 + result2
