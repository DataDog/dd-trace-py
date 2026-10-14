"""Legacy re-export shim for a moved module.

``ddtrace.testing.internal.http`` moved to:

``ddtrace_testing.internal.http``

Keep the historical import path working for external consumers; import from
the new module instead.
"""

from ddtrace_testing.internal.http import DEFAULT_TIMEOUT_SECONDS as DEFAULT_TIMEOUT_SECONDS  # noqa: F401
from ddtrace_testing.internal.http import MAX_ATTEMPTS as MAX_ATTEMPTS  # noqa: F401
from ddtrace_testing.internal.http import MAX_RETRY_AFTER_SECONDS as MAX_RETRY_AFTER_SECONDS  # noqa: F401
from ddtrace_testing.internal.http import RETRIABLE_ERRORS as RETRIABLE_ERRORS  # noqa: F401
from ddtrace_testing.internal.http import BackendConnector as BackendConnector  # noqa: F401
from ddtrace_testing.internal.http import BackendConnectorAgentlessSetup as BackendConnectorAgentlessSetup  # noqa: F401
from ddtrace_testing.internal.http import BackendConnectorEVPProxySetup as BackendConnectorEVPProxySetup  # noqa: F401
from ddtrace_testing.internal.http import BackendConnectorSetup as BackendConnectorSetup  # noqa: F401
from ddtrace_testing.internal.http import BackendError as BackendError  # noqa: F401
from ddtrace_testing.internal.http import BackendResult as BackendResult  # noqa: F401
from ddtrace_testing.internal.http import FileAttachment as FileAttachment  # noqa: F401
from ddtrace_testing.internal.http import NoOpBackendConnector as NoOpBackendConnector  # noqa: F401
from ddtrace_testing.internal.http import NoOpBackendConnectorSetup as NoOpBackendConnectorSetup  # noqa: F401
from ddtrace_testing.internal.http import Subdomain as Subdomain  # noqa: F401
from ddtrace_testing.internal.http import T as T  # noqa: F401
from ddtrace_testing.internal.http import UnixDomainSocketHTTPConnection as UnixDomainSocketHTTPConnection  # noqa: F401
from ddtrace_testing.internal.http import log as log  # noqa: F401
