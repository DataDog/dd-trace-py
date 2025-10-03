"""Minimal bytecode instrumentation for fast file coverage."""

import sys


def add_file_coverage_hook(code, hook_func, file_path):
    """Add a single hook call at the beginning of a code object.
    
    This delegates to version-specific implementations.
    """
    
    # Import the appropriate version-specific implementation
    if sys.version_info >= (3, 14):
        from .fast_instrumentation_py3_14 import add_file_coverage_hook as impl
    elif sys.version_info >= (3, 13):
        from .fast_instrumentation_py3_13 import add_file_coverage_hook as impl
    elif sys.version_info >= (3, 12):
        from .fast_instrumentation_py3_12 import add_file_coverage_hook as impl
    elif sys.version_info >= (3, 11):
        from .fast_instrumentation_py3_11 import add_file_coverage_hook as impl
    elif sys.version_info >= (3, 10):
        from .fast_instrumentation_py3_10 import add_file_coverage_hook as impl
    else:
        # Python 3.8 and 3.9 use the same implementation
        from .fast_instrumentation_py3_8 import add_file_coverage_hook as impl
    
    return impl(code, hook_func, file_path)
