"""Legacy re-export shim for a moved module.

``ddtrace.testing.internal.cached_file_provider`` moved to:

``ddtrace_testing.internal.cached_file_provider``

Keep the historical import path working for external consumers; import from
the new module instead.
"""

from ddtrace_testing.internal.cached_file_provider import CachedFileDataProvider as CachedFileDataProvider  # noqa: F401
from ddtrace_testing.internal.cached_file_provider import TestOptDataProvider as TestOptDataProvider  # noqa: F401
from ddtrace_testing.internal.cached_file_provider import _read_cache_json as _read_cache_json  # noqa: F401
from ddtrace_testing.internal.cached_file_provider import log as log  # noqa: F401
