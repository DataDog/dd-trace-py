from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ddtrace.profiling.profiler import Profiler

# Note: profiler may not exist in the module (we do setattr's and del's
# on it. However, having it this way is better than not having it at all.
profiler: Profiler
