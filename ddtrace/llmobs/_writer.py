import atexit
import json
from typing import Any
from typing import Dict
from typing import List
from typing import Union


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


class LLMObsSpanEvent(TypedDict):
    span_id: str
    trace_id: str
    parent_id: str
    session_id: str
    tags: List[str]
    service: str
    name: str
    start_ns: int
    duration: float
    status: str
    status_message: str
    meta: Dict[str, Any]
    metrics: Dict[str, Any]


class LLMObsEvaluationMetricEvent(TypedDict, total=False):
    span_id: str
    trace_id: str
    metric_type: str
    label: str
    categorical_value: str
    numerical_value: float
    score_value: float
    tags: List[str]


class BaseLLMObsWriter(PeriodicService):
    """Base writer class for submitting data to Datadog LLMObs endpoints."""

    def __init__(self, site: str, api_key: str, interval: float, timeout: float) -> None:
        super(BaseLLMObsWriter, self).__init__(interval=interval)
        self._lock = forksafe.RLock()
        self._buffer = []  # type: List[Union[LLMObsSpanEvent, LLMObsEvaluationMetricEvent]]
        self._buffer_limit = 1000
        self._timeout = timeout  # type: float
        self._api_key = api_key or ""  # type: str
        self._endpoint = ""  # type: str
        self._site = site  # type: str
        self._intake = ""  # type: str
        self._headers = {"DD-API-KEY": self._api_key, "Content-Type": "application/json"}
        self._event_type = ""  # type: str

    def start(self, *args, **kwargs):
        super(BaseLLMObsWriter, self).start()
        logger.debug("started %r to %r", self.__class__.__name__, self._url)
        atexit.register(self.on_shutdown)

    def on_shutdown(self):
        self.periodic()

    def _enqueue(self, event: Union[LLMObsSpanEvent, LLMObsEvaluationMetricEvent]) -> None:
        with self._lock:
            if len(self._buffer) >= self._buffer_limit:
                logger.warning(
                    "%r event buffer full (limit is %d), dropping event", self.__class__.__name__, self._buffer_limit
                )
                return
            self._buffer.append(event)

    def periodic(self) -> None:
        with self._lock:
            if not self._buffer:
                return
            events = self._buffer
            self._buffer = []

        data = self._data(events)
        try:
            enc_llm_events = json.dumps(data)
        except TypeError:
            logger.error("failed to encode %d LLMObs %s events", len(events), self._event_type, exc_info=True)
            return
        conn = httplib.HTTPSConnection(self._intake, 443, timeout=self._timeout)
        try:
            conn.request("POST", self._endpoint, enc_llm_events, self._headers)
            resp = get_connection_response(conn)
            if resp.status >= 300:
                logger.error(
                    "failed to send %d LLMObs %s events to %s, got response code %d, status: %s",
                    len(events),
                    self._event_type,
                    self._url,
                    resp.status,
                    resp.read(),
                )
            else:
                logger.debug("sent %d LLMObs %s events to %s", len(events), self._event_type, self._url)
        except Exception:
            logger.error(
                "failed to send %d LLMObs %s events to %s", len(events), self._event_type, self._intake, exc_info=True
            )
        finally:
            conn.close()

    @property
    def _url(self) -> str:
        return "https://%s%s" % (self._intake, self._endpoint)

    def _data(self, events: List[Any]) -> Dict[str, Any]:
        raise NotImplementedError


class LLMObsSpanWriter(BaseLLMObsWriter):
    """Writer to the Datadog LLMObs Span Event Endpoint."""

    def __init__(self, site: str, api_key: str, interval: float, timeout: float) -> None:
        super(LLMObsSpanWriter, self).__init__(site, api_key, interval, timeout)
        self._event_type = "span"
        self._buffer = []
        self._endpoint = "/api/v2/llmobs"  # type: str
        self._intake = "llmobs-intake.%s" % self._site  # type: str

    def enqueue(self, event: LLMObsSpanEvent) -> None:
        self._enqueue(event)

    def _data(self, events: List[LLMObsSpanEvent]) -> Dict[str, Any]:
        return {"ml_obs": {"stage": "raw", "type": "span", "spans": events}}


class LLMObsEvalMetricWriter(BaseLLMObsWriter):
    """Writer to the Datadog LLMObs Custom Eval Metrics Endpoint."""

    def __init__(self, site: str, api_key: str, interval: float, timeout: float) -> None:
        super(LLMObsEvalMetricWriter, self).__init__(site, api_key, interval, timeout)
        self._event_type = "evaluation_metric"
        self._buffer = []
        self._endpoint = "/api/unstable/llm-obs/v1/eval-metric"
        self._intake = "api.%s" % self._site  # type: str

    def enqueue(self, event: LLMObsEvaluationMetricEvent) -> None:
        self._enqueue(event)

    def _data(self, events: List[LLMObsEvaluationMetricEvent]) -> Dict[str, Any]:
        return {"data": {"type": "evaluation_metric", "attributes": {"metrics": events}}}
