import os
import sys


# Check if file-level coverage is requested (Python 3.12+ only)
# File-level coverage uses PY_START events instead of LINE events for much better performance
# when you only need to know which files were executed, not which specific lines
_USE_FILE_LEVEL_COVERAGE = os.environ.get("_DD_COVERAGE_FILE_LEVEL", "").lower() == "true"


# Import are noqa'd otherwise some formatters will helpfully remove them
if sys.version_info >= (3, 14):
    from ddtrace.internal.coverage.instrumentation_py3_14 import instrument_all_lines  # noqa
elif sys.version_info >= (3, 12):
    if _USE_FILE_LEVEL_COVERAGE:
        # Use file-level coverage for better performance (PY_START events)
        from ddtrace.internal.coverage.instrumentation_py3_12_filelevel import (
            instrument_for_file_coverage as instrument_all_lines,  # noqa
        )
    else:
        # Use line-level coverage for detailed coverage data (LINE events)
        from ddtrace.internal.coverage.instrumentation_py3_12 import instrument_all_lines  # noqa
elif sys.version_info >= (3, 11):
    from ddtrace.internal.coverage.instrumentation_py3_11 import instrument_all_lines  # noqa
elif sys.version_info >= (3, 10):
    from ddtrace.internal.coverage.instrumentation_py3_10 import instrument_all_lines  # noqa
else:
    # Python 3.8 and 3.9 use the same instrumentation
    from ddtrace.internal.coverage.instrumentation_py3_8 import instrument_all_lines  # noqa
