import os
from typing import Dict
from typing import Optional

from ddtrace import config as tracer_config
from ddtrace.internal.agent import get_trace_url
from ddtrace.internal.constants import DEFAULT_SERVICE_NAME
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.config import get_application_name
from ddtrace.internal.utils.formats import asbool
from ddtrace.version import get_version


log = get_logger(__name__)


DEFAULT_DEBUGGER_PORT = 8126
DEFAULT_MAX_PROBES = 100
DEFAULT_METRICS = True
DEFAULT_GLOBAL_RATE_LIMIT = 100.0
DEFAULT_MAX_PAYLOAD_SIZE = 1 << 20  # 1 MB
DEFAULT_CONFIG_TIMEOUT = 30  # s
DEFAULT_UPLOAD_TIMEOUT = 30  # seconds
DEFAULT_UPLOAD_FLUSH_INTERVAL = 1.0  # seconds
DEFAULT_PROBE_POLL_INTERVAL = 1.0  # seconds
DEFAULT_DIAGNOSTIC_INTERVAL = 3600  # 1 hour


class DebuggerConfig(object):
    """Debugger configuration."""

    service_name = DEFAULT_SERVICE_NAME
    probe_url = get_trace_url()
    snapshot_intake_url = get_trace_url()
    max_probes = DEFAULT_MAX_PROBES
    metrics = DEFAULT_METRICS
    global_rate_limit = DEFAULT_GLOBAL_RATE_LIMIT
    max_payload_size = DEFAULT_MAX_PAYLOAD_SIZE
    config_timeout = DEFAULT_CONFIG_TIMEOUT
    upload_timeout = DEFAULT_UPLOAD_TIMEOUT
    upload_flush_interval = DEFAULT_UPLOAD_FLUSH_INTERVAL
    poll_interval = DEFAULT_PROBE_POLL_INTERVAL
    diagnostic_interval = DEFAULT_DIAGNOSTIC_INTERVAL
    tags = None  # type: Optional[str]
    _tags = {}  # type: Dict[str, str]
    _tags_in_qs = True
    _snapshot_intake_endpoint = "/v1/input"

    def __init__(self):
        # type: () -> None
        try:
            self.snapshot_intake_url = os.environ["DD_DEBUGGER_SNAPSHOT_INTAKE_URL"]
            self._tags_in_qs = False
        except KeyError:
            self.snapshot_intake_url = get_trace_url()
            self._snapshot_intake_endpoint = "/debugger" + self._snapshot_intake_endpoint

        self.probe_url = os.getenv("DD_DEBUGGER_PROBE_URL", get_trace_url())
        self.upload_timeout = int(os.getenv("DD_DEBUGGER_UPLOAD_TIMEOUT", DEFAULT_UPLOAD_TIMEOUT))
        self.upload_flush_interval = float(
            os.getenv("DD_DEBUGGER_UPLOAD_FLUSH_INTERVAL", DEFAULT_UPLOAD_FLUSH_INTERVAL)
        )

        self._tags["env"] = tracer_config.env
        self._tags["version"] = tracer_config.version
        self._tags["debugger_version"] = get_version()

        self._tags.update(tracer_config.tags)

        self.tags = ",".join([":".join((k, v)) for (k, v) in self._tags.items() if v is not None])

        self.service_name = tracer_config.service or get_application_name() or DEFAULT_SERVICE_NAME
        self.metrics = asbool(os.getenv("DD_DEBUGGER_METRICS_ENABLED", DEFAULT_METRICS))
        self.max_payload_size = int(os.getenv("DD_DEBUGGER_MAX_PAYLOAD_SIZE", DEFAULT_MAX_PAYLOAD_SIZE))

        self.config_timeout = int(os.getenv("DD_DEBUGGER_CONFIG_TIMEOUT", DEFAULT_CONFIG_TIMEOUT))
        self.poll_interval = int(os.getenv("DD_DEBUGGER_POLL_INTERVAL", DEFAULT_PROBE_POLL_INTERVAL))
        self.diagnostic_interval = int(os.getenv("DD_DEBUGGER_DIAGNOSTIC_INTERVAL", DEFAULT_DIAGNOSTIC_INTERVAL))

        log.debug(
            "Debugger configuration: %r",
            {k: v for k, v in ((k, getattr(self, k)) for k in type(self).__dict__ if not k.startswith("__"))},
        )


config = DebuggerConfig()
