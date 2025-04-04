import atexit
from typing import Any
from typing import Dict
from typing import List
from typing import Union


# TypedDict was added to typing in python 3.8
try:
    from typing import TypedDict
except ImportError:
    from typing_extensions import TypedDict
import http.client as httplib

import ddtrace
from ddtrace import config
from ddtrace.internal import forksafe
from ddtrace.internal import service
from ddtrace.internal.logger import get_logger
from ddtrace.internal.periodic import PeriodicService
from ddtrace.internal.utils.http import get_connection
from ddtrace.internal.utils.retry import fibonacci_backoff_with_jitter
from ddtrace.llmobs import _telemetry as telemetry
from ddtrace.llmobs._constants import AGENTLESS_BASE_URL
from ddtrace.llmobs._constants import AGENTLESS_EVAL_ENDPOINT
from ddtrace.llmobs._constants import AGENTLESS_SPAN_ENDPOINT
from ddtrace.llmobs._constants import DROPPED_IO_COLLECTION_ERROR
from ddtrace.llmobs._constants import DROPPED_VALUE_TEXT
from ddtrace.llmobs._constants import EVP_EVENT_SIZE_LIMIT
from ddtrace.llmobs._constants import EVP_PAYLOAD_SIZE_LIMIT
from ddtrace.llmobs._constants import EVP_PROXY_AGENT_ENDPOINT
from ddtrace.llmobs._constants import EVP_SUBDOMAIN_HEADER_NAME
from ddtrace.llmobs._constants import EVP_SUBDOMAIN_HEADER_VALUE
from ddtrace.llmobs._utils import safe_json
from ddtrace.settings._agent import config as agent_config


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
    collection_errors: List[str]
    _dd: Dict[str, str]


class LLMObsEvaluationMetricEvent(TypedDict, total=False):
    join_on: Dict[str, Dict[str, str]]
    metric_type: str
    label: str
    categorical_value: str
    numerical_value: float
    score_value: float
    ml_app: str
    timestamp_ms: int
    tags: List[str]


class BaseLLMObsWriter(PeriodicService):
    """Base writer class for submitting data to Datadog LLMObs endpoints."""

    RETRY_ATTEMPTS = 3
    EVENT_TYPE = ""

    def __init__(self, site: str, api_key: str, interval: float, timeout: float, is_agentless: bool = True) -> None:
        super(BaseLLMObsWriter, self).__init__(interval=interval)
        self._lock = forksafe.RLock()
        self._buffer = []  # type: List[Union[LLMObsSpanEvent, LLMObsEvaluationMetricEvent]]
        self._buffer_limit = 1000
        self._timeout = timeout  # type: float
        self._api_key = api_key or ""  # type: str
        self._endpoint = ""  # type: str
        self._site = site  # type: str
        self._intake = ""  # type: str
        self._headers = {"Content-Type": "application/json"}
        self._agentless = is_agentless
        if is_agentless:
            self._headers["DD-API-KEY"] = self._api_key
            self._intake = "api.{}".format(self._site)
        else:
            self._headers[EVP_SUBDOMAIN_HEADER_NAME] = EVP_SUBDOMAIN_HEADER_VALUE
            self._intake = agent_config.trace_agent_url.split("//")[-1]

        self._send_payload_with_retry = fibonacci_backoff_with_jitter(
            attempts=self.RETRY_ATTEMPTS,
            initial_wait=0.618 * self.interval / (1.618 ** self.RETRY_ATTEMPTS) / 2,
            until=lambda result: isinstance(result, httplib.HTTPResponse),
        )(self._send_payload)

    def start(self, *args, **kwargs):
        super(BaseLLMObsWriter, self).start()
        logger.debug("started %r to %r", self.__class__.__name__, self._url)
        atexit.register(self.on_shutdown)

    def on_shutdown(self):
        self.periodic()

    def _enqueue(self, event: Union[LLMObsSpanEvent, LLMObsEvaluationMetricEvent]) -> None:
        event_size = len(safe_json(event))

        with self._lock:
            if len(self._buffer) >= self._buffer_limit:
                logger.warning(
                    "%r event buffer full (limit is %d), dropping event", self.__class__.__name__, self._buffer_limit
                )
                telemetry.record_dropped_payload(1, event_type=self.EVENT_TYPE, error="buffer_full")
                return
            if len(self._buffer) + event_size > EVP_PAYLOAD_SIZE_LIMIT:
                logger.debug("manually flushing buffer because queueing next event will exceed EVP payload limit")
                self.periodic()
            if self.EVENT_TYPE == "span":
                telemetry.record_span_event_size(event, event_size)
            self._buffer.append(event)

    def _encode(self, payload, num_events):
        try:
            enc_llm_events = safe_json(payload)
            logger.debug("encoded %d LLMObs %s events to be sent", num_events, self.EVENT_TYPE)
            return enc_llm_events
        except TypeError:
            logger.error("failed to encode %d LLMObs %s events", num_events, self.EVENT_TYPE, exc_info=True)
            telemetry.record_dropped_payload(num_events, event_type=self.EVENT_TYPE, error="encoding_error")
            return None

    def periodic(self) -> None:
        with self._lock:
            if not self._buffer:
                return
            events = self._buffer
            self._buffer = []

        if self._agentless and not self._headers.get("DD-API-KEY"):
            logger.warning(
                "DD_API_KEY is required for sending evaluation metrics. Evaluation metric data will not be sent. "
                "Ensure this configuration is set before running your application."
            )
            return
        data = self._data(events)
        enc_llm_events = self._encode(data, len(events))
        if not enc_llm_events:
            return
        try:
            self._send_payload_with_retry(enc_llm_events, len(events))
        except Exception:
            telemetry.record_dropped_payload(len(events), event_type=self.EVENT_TYPE, error="connection_error")
            logger.error(
                "failed to send %d LLMObs %s events to %s", len(events), self.EVENT_TYPE, self._intake, exc_info=True
            )

    def _send_payload(self, payload: bytes, num_events: int):
        conn = get_connection(self._intake, self._timeout)
        try:
            conn.request("POST", self._endpoint, payload, self._headers)
            resp = conn.getresponse()
            if resp.status >= 300:
                logger.error(
                    "failed to send %d LLMObs %s events to %s, got response code %d, status: %s",
                    num_events,
                    self.EVENT_TYPE,
                    self._url,
                    resp.status,
                    resp.read(),
                )
                telemetry.record_dropped_payload(num_events, event_type=self.EVENT_TYPE, error="http_error")
            else:
                logger.debug("sent %d LLMObs %s events to %s", num_events, self.EVENT_TYPE, self._url)
            return resp
        except Exception:
            logger.error(
                "failed to send %d LLMObs %s events to %s", num_events, self.EVENT_TYPE, self._intake, exc_info=True
            )
            raise
        finally:
            conn.close()

    @property
    def _url(self) -> str:
        return "{}{}".format(self._intake, self._endpoint)

    def _data(self, events: List[Any]) -> Dict[str, Any]:
        raise NotImplementedError

    def recreate(self) -> "BaseLLMObsWriter":
        return self.__class__(
            site=self._site,
            api_key=self._api_key,
            interval=self._interval,
            timeout=self._timeout,
        )


class LLMObsEvalMetricWriter(BaseLLMObsWriter):
    """Writer to the Datadog LLMObs Custom Eval Metrics Endpoint."""
    EVENT_TYPE = "evaluation_metric"

    def __init__(self, site: str, api_key: str, interval: float, timeout: float) -> None:
        super(LLMObsEvalMetricWriter, self).__init__(site, api_key, interval, timeout, is_agentless=True)
        self._buffer = []
        self._endpoint = AGENTLESS_EVAL_ENDPOINT


    def enqueue(self, event: LLMObsEvaluationMetricEvent) -> None:
        self._enqueue(event)

    def _data(self, events: List[LLMObsEvaluationMetricEvent]) -> Dict[str, Any]:
        return {"data": {"type": "evaluation_metric", "attributes": {"metrics": events}}}


class LLMObsSpanWriter(BaseLLMObsWriter):
    """Writer to the Datadog LLMObs Span Endpoint via Agent EvP Proxy."""

    EVENT_TYPE = "span"

    def __init__(
        self, site: str, api_key: str, interval: float, timeout: float, is_agentless: bool = True, _agentless_url: str = ""
    ) -> None:
        super(LLMObsSpanWriter, self).__init__(site, api_key, interval, timeout, is_agentless)
        self.agentless_url = _agentless_url
        if is_agentless:
            if not _agentless_url:
                raise ValueError("_agentless_url is required for agentless mode")
            self._intake = _agentless_url or AGENTLESS_BASE_URL
            self._endpoint = AGENTLESS_SPAN_ENDPOINT
        else:
            self._intake = agent_config.trace_agent_url
            self._endpoint = EVP_PROXY_AGENT_ENDPOINT

    def start(self, *args, **kwargs):
        super(LLMObsSpanWriter, self).start()
        logger.debug("started %r to %r", self.__class__.__name__, self._url)
        atexit.register(self.on_shutdown)

    def stop(self, timeout=None):
        if self.status != service.ServiceStatus.STOPPED:
            super(LLMObsSpanWriter, self).stop(timeout=timeout)

    def enqueue(self, event: LLMObsSpanEvent) -> None:
        raw_event_size = len(safe_json(event))
        should_truncate = raw_event_size >= EVP_EVENT_SIZE_LIMIT
        if should_truncate:
            logger.warning(
                "dropping event input/output because its size (%d) exceeds the event size limit (1MB)",
                raw_event_size,
            )
            event = _truncate_span_event(event)
        telemetry.record_span_event_raw_size(event, raw_event_size)
        self._enqueue(event)

    def _data(self, events: List[LLMObsSpanEvent]) -> Dict[str, Any]:
        return {"_dd.stage": "raw", "_dd.tracer_version": ddtrace.__version__, "event_type": "span", "spans": events}

    def recreate(self) -> "LLMObsSpanWriter":
        return self.__class__(
            site=self._site,
            api_key=self._api_key,
            interval=self._interval,
            timeout=self._timeout,
            is_agentless=config._llmobs_agentless_enabled,
            _agentless_url=self.agentless_url,
        )


def _truncate_span_event(event: LLMObsSpanEvent) -> LLMObsSpanEvent:
    event["meta"]["input"] = {"value": DROPPED_VALUE_TEXT}
    event["meta"]["output"] = {"value": DROPPED_VALUE_TEXT}

    event["collection_errors"] = [DROPPED_IO_COLLECTION_ERROR]
    return event
