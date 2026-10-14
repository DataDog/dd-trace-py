"""Legacy re-export shim for a moved module.

``ddtrace.testing.internal.pytest.utils`` moved to:

``ddtrace_testing.internal.pytest.utils``

Keep the historical import path working for external consumers; import from
the new module instead.
"""

from ddtrace_testing.internal.pytest.utils import _encode_test_parameter as _encode_test_parameter  # noqa: F401
from ddtrace_testing.internal.pytest.utils import _get_test_parameters_json as _get_test_parameters_json  # noqa: F401
from ddtrace_testing.internal.pytest.utils import item_to_test_ref as item_to_test_ref  # noqa: F401
from ddtrace_testing.internal.pytest.utils import log as log  # noqa: F401
from ddtrace_testing.internal.pytest.utils import nodeid_to_names as nodeid_to_names  # noqa: F401
