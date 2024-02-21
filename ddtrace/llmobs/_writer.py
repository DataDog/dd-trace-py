import atexit
import json
from typing import Any
from typing import Dict
from typing import List


# TypedDict was added to typing in python 3.8
try:
    from typing import TypedDict
except ImportError:
    from typing_extensions import TypedDict

from ddtrace.internal import forksafe
from ddtrace.internal.compat import get_connection_response
from ddtrace.internal.compat import httplib
from ddtrace.internal.logger import get_logger
from ddtrace.internal.periodic import PeriodicService


logger = get_logger(__name__)


class LLMObsEvent(TypedDict):
    span_id: str
    trace_id: str
    parent_id: str
    session_id: str
    tags: List[str]
    service: str
    name: str
    error: int
    start_ns: int
    duration: float
    status: str
    status_message: str
    meta: Dict[str, Any]
    metrics: Dict[str, Any]


class LLMObsWriter(PeriodicService):
    """Writer to the Datadog LLMObs intake."""

    def __init__(self, site: str, api_key: str, interval: float, timeout: float) -> None:
        super(LLMObsWriter, self).__init__(interval=interval)
        self._lock = forksafe.RLock()
        self._buffer = []  # type: List[LLMObsEvent]
        self._buffer_limit = 1000
        self._timeout = timeout  # type: float
        self._api_key = api_key or ""  # type: str
        self._endpoint = "/api/v2/llmobs"  # type: str
        self._site = site  # type: str
        self._intake = "llmobs-intake.%s" % self._site  # type: str
        self._headers = {"DD-API-KEY": self._api_key, "Content-Type": "application/json"}

    def start(self, *args, **kwargs):
        super(LLMObsWriter, self).start()
        logger.debug("started llmobs writer to %r", self._url)
        atexit.register(self.on_shutdown)

    def enqueue(self, event: LLMObsEvent) -> None:
        with self._lock:
            if len(self._buffer) >= self._buffer_limit:
                logger.warning("LLMobs event buffer full (limit is %d), dropping record", self._buffer_limit)
                return
            self._buffer.append(event)

    def on_shutdown(self):
        self.periodic()

    @property
    def _url(self) -> str:
        return "https://%s%s" % (self._intake, self._endpoint)

    def periodic(self) -> None:
        with self._lock:
            if not self._buffer:
                return
            events = self._buffer
            self._buffer = []

        data = {"ml_obs": {"stage": "raw", "type": "span", "spans": events}}
        try:
            enc_llm_events = json.dumps(data)
        except TypeError:
            logger.error("failed to encode %d LLMObs events", len(events), exc_info=True)
            return
        conn = httplib.HTTPSConnection(self._intake, 443, timeout=self._timeout)
        try:
            conn.request("POST", self._endpoint, enc_llm_events, self._headers)
            resp = get_connection_response(conn)
            if resp.status >= 300:
                logger.error(
                    "failed to send %d LLMObs events to %r, got response code %r, status: %r",
                    len(events),
                    self._url,
                    resp.status,
                    resp.read(),
                )
            else:
                logger.debug("sent %d LLMObs events to %r", len(events), self._url)
        except Exception:
            logger.error("failed to send %d LLMObs events to %r", len(events), self._intake, exc_info=True)
        finally:
            conn.close()
