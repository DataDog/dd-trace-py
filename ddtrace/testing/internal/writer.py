"""Legacy re-export shim for a moved module.

``ddtrace.testing.internal.writer`` moved to:

``ddtrace_testing.internal.writer``

Keep the historical import path working for external consumers; import from
the new module instead.
"""

from ddtrace_testing.internal.writer import BaseWriter as BaseWriter  # noqa: F401
from ddtrace_testing.internal.writer import Event as Event  # noqa: F401
from ddtrace_testing.internal.writer import EventSerializer as EventSerializer  # noqa: F401
from ddtrace_testing.internal.writer import PayloadFileCoverageWriter as PayloadFileCoverageWriter  # noqa: F401
from ddtrace_testing.internal.writer import PayloadFileTestOptWriter as PayloadFileTestOptWriter  # noqa: F401
from ddtrace_testing.internal.writer import TestCoverageWriter as TestCoverageWriter  # noqa: F401
from ddtrace_testing.internal.writer import TestOptWriter as TestOptWriter  # noqa: F401
from ddtrace_testing.internal.writer import TSerializable as TSerializable  # noqa: F401
from ddtrace_testing.internal.writer import _calculate_async_flush_events as _calculate_async_flush_events  # noqa: F401
from ddtrace_testing.internal.writer import _get_async_flush_events as _get_async_flush_events  # noqa: F401
from ddtrace_testing.internal.writer import _get_min_flush_events as _get_min_flush_events  # noqa: F401
from ddtrace_testing.internal.writer import _truncate_events_meta as _truncate_events_meta  # noqa: F401
from ddtrace_testing.internal.writer import _truncate_payload_metadata as _truncate_payload_metadata  # noqa: F401
from ddtrace_testing.internal.writer import log as log  # noqa: F401
from ddtrace_testing.internal.writer import serialize_module as serialize_module  # noqa: F401
from ddtrace_testing.internal.writer import serialize_session as serialize_session  # noqa: F401
from ddtrace_testing.internal.writer import serialize_suite as serialize_suite  # noqa: F401
from ddtrace_testing.internal.writer import serialize_test_run as serialize_test_run  # noqa: F401
