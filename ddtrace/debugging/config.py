import os
from typing import Dict
from typing import Optional

from ddtrace import config as tracer_config
from ddtrace.internal.constants import DEFAULT_SERVICE_NAME
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.config import get_application_name
from ddtrace.internal.utils.formats import asbool
from ddtrace.version import get_version


log = get_logger(__name__)


DD_SITE_MAP = {"datad0g.com": "https://dd.datad0g.com"}

DEFAULT_DEBUGGER_PORT = 8126
DEFAULT_PROBE_API_URL = "https://app.datadoghq.com"
DEFAULT_SNAPSHOT_INTAKE_URL = "http://%s:%d" % (os.getenv("DD_AGENT_HOST", "localhost"), DEFAULT_DEBUGGER_PORT)
DEFAULT_MAX_PROBES = 100
DEFAULT_METRICS = True
DEFAULT_PROBE_STATUS_URL = DEFAULT_SNAPSHOT_INTAKE_URL
DEFAULT_GLOBAL_RATE_LIMIT = 100.0
DEFAULT_AGENT_MODE = False
DEFAULT_API_KEY_FILE = "/etc/datadog/secrets/datadog-api-key"


def _get_api_key_from_file():
    # type: () -> Optional[str]
    """Retrieve the API key from file."""
    try:
        key_file = os.getenv("DD_API_KEY_FILE", DEFAULT_API_KEY_FILE)
        with open(key_file) as _:
            return _.read().strip()
    except (IOError, OSError):
        return None


SECRETS = frozenset(["api_key"])


class DebuggerConfig(object):
    """Debugger configuration."""

    api_key = None  # type: Optional[str]
    service_name = DEFAULT_SERVICE_NAME
    probe_api_url = DEFAULT_PROBE_API_URL
    snapshot_intake_url = DEFAULT_SNAPSHOT_INTAKE_URL
    probe_status_url = DEFAULT_PROBE_STATUS_URL
    max_probes = DEFAULT_MAX_PROBES
    metrics = DEFAULT_METRICS
    global_rate_limit = DEFAULT_GLOBAL_RATE_LIMIT
    tags = None  # type: Optional[str]
    _tags = {}  # type: Dict[str, str]
    _tags_in_qs = True
    _snapshot_intake_endpoint = "/v1/input"
    _probe_status_endpoint = _snapshot_intake_endpoint
    _agent = DEFAULT_AGENT_MODE

    def __init__(self):
        # type: () -> None
        api_key = os.getenv("DD_API_KEY", None)
        if api_key is None:
            api_key = _get_api_key_from_file()
            if api_key is not None:
                log.debug("API key retrieved from the file system")
        self.api_key = api_key

        try:
            self.snapshot_intake_url = os.environ["DD_DEBUGGER_SNAPSHOT_INTAKE_URL"]
            self._tags_in_qs = False
        except KeyError:
            self.snapshot_intake_url = DEFAULT_SNAPSHOT_INTAKE_URL
            self._snapshot_intake_endpoint = "/debugger" + self._snapshot_intake_endpoint

        self.probe_status_url = self.snapshot_intake_url
        self._probe_status_endpoint = self._snapshot_intake_endpoint

        dd_site = os.getenv("DD_SITE", None)
        if dd_site:
            self.probe_api_url = DD_SITE_MAP.get(dd_site) or ("https://app.%s" % dd_site)
        else:
            self.probe_api_url = os.getenv("DD_DEBUGGER_PROBE_API_URL", DEFAULT_PROBE_API_URL)

        self._tags["env"] = tracer_config.env
        self._tags["version"] = tracer_config.version
        self._tags["debugger_version"] = get_version()

        self._tags.update(tracer_config.tags)

        self.tags = ",".join([":".join((k, v)) for (k, v) in self._tags.items() if v is not None])

        log.debug(
            "Debugger configuration: %r",
            {
                k: v[0:3] + "***" if v is not None and k in SECRETS else v
                for k, v in ((k, getattr(self, k)) for k in type(self).__dict__ if not k.startswith("__"))
            },
        )

        self.service_name = tracer_config.service or get_application_name() or DEFAULT_SERVICE_NAME
        self.metrics = asbool(os.getenv("DD_DEBUGGER_METRICS_ENABLED", DEFAULT_METRICS))
        self._agent = asbool(os.getenv("DD_DEBUGGER_AGENT_MODE", DEFAULT_AGENT_MODE))


config = DebuggerConfig()
