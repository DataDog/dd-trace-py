"""The native sender used by every debugger egress path.

Dynamic Instrumentation, Exception Replay, Code Origin, probe diagnostics and the
symbol database all upload through the ``datadog-live-debugger`` sender wrapped by
:class:`ddtrace.internal.native.DebuggerSender`. The native side derives the
per-track path from a single endpoint, so the only decision made here is *which*
endpoint: the local trace agent, or ``debugger-intake.{DD_SITE}`` directly when
agentless submission is enabled via ``_DD_APM_TRACING_AGENTLESS_ENABLED``.

Each consumer builds its own sender. They are cheap (an endpoint configuration
plus a handle on the shared native runtime — the underlying HTTP client is built
per request either way), and the one piece of mutable state, the downgrade to the
diagnostics endpoint, only ever concerns the logs and snapshots tracks that the
signal uploader owns.
"""

from ddtrace import config as ddconfig
from ddtrace.internal.logger import get_logger
from ddtrace.internal.native import DebuggerSender
from ddtrace.internal.native_runtime import get_native_runtime
from ddtrace.internal.settings.dynamic_instrumentation import config as di_config
from ddtrace.internal.utils.formats import get_test_session_token


log = get_logger(__name__)


def build_debugger_sender() -> DebuggerSender:
    """Build a sender for the configured intake."""
    timeout_ms = int(di_config.upload_timeout * 1000)

    if di_config._agentless:
        # The native side derives https://debugger-intake.{site} from the site.
        sender = DebuggerSender(
            get_native_runtime(),
            site=ddconfig._dd_site,
            api_key=ddconfig._dd_api_key,
            tags=di_config.tags,
            timeout_ms=timeout_ms,
            test_session_token=get_test_session_token(),
        )
    else:
        sender = DebuggerSender(
            get_native_runtime(),
            url=di_config._intake_url,
            tags=di_config.tags,
            timeout_ms=timeout_ms,
            test_session_token=get_test_session_token(),
        )

    log.debug("Debugger sender initialized: %r", sender)

    return sender
