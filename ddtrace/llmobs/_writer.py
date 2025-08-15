import atexit
import json
import os
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple
from typing import Union
from typing import cast
from urllib.parse import quote


# TypedDict was added to typing in python 3.8
try:
    from typing import TypedDict
except ImportError:
    from typing_extensions import TypedDict
from typing_extensions import NotRequired

import ddtrace
from ddtrace import config
from ddtrace.internal import agent
from ddtrace.internal import forksafe
from ddtrace.internal.logger import get_logger
from ddtrace.internal.periodic import PeriodicService
from ddtrace.internal.utils.http import Response
from ddtrace.internal.utils.http import get_connection
from ddtrace.internal.utils.retry import fibonacci_backoff_with_jitter
from ddtrace.llmobs import _telemetry as telemetry
from ddtrace.llmobs._constants import AGENTLESS_EVAL_BASE_URL
from ddtrace.llmobs._constants import AGENTLESS_EXP_BASE_URL
from ddtrace.llmobs._constants import AGENTLESS_SPAN_BASE_URL
from ddtrace.llmobs._constants import DROPPED_IO_COLLECTION_ERROR
from ddtrace.llmobs._constants import DROPPED_VALUE_TEXT
from ddtrace.llmobs._constants import EVAL_ENDPOINT
from ddtrace.llmobs._constants import EVAL_SUBDOMAIN_NAME
from ddtrace.llmobs._constants import EVP_EVENT_SIZE_LIMIT
from ddtrace.llmobs._constants import EVP_PAYLOAD_SIZE_LIMIT
from ddtrace.llmobs._constants import EVP_PROXY_AGENT_BASE_PATH
from ddtrace.llmobs._constants import EVP_SUBDOMAIN_HEADER_NAME
from ddtrace.llmobs._constants import EXP_SUBDOMAIN_NAME
from ddtrace.llmobs._constants import SPAN_ENDPOINT
from ddtrace.llmobs._constants import SPAN_SUBDOMAIN_NAME
from ddtrace.llmobs._experiment import Dataset
from ddtrace.llmobs._experiment import DatasetRecord
from ddtrace.llmobs._experiment import DatasetRecordRaw
from ddtrace.llmobs._experiment import JSONType
from ddtrace.llmobs._experiment import UpdatableDatasetRecord
from ddtrace.llmobs._utils import safe_json
from ddtrace.settings._agent import config as agent_config


logger = get_logger(__name__)


class LLMObsSpanEvent(TypedDict):
    span_id: str
    trace_id: str
    parent_id: str
    session_id: NotRequired[str]
    tags: List[str]
    service: NotRequired[str]
    name: str
    start_ns: int
    duration: int
    status: str
    status_message: NotRequired[str]
    meta: Dict[str, Any]
    metrics: Dict[str, Any]
    collection_errors: NotRequired[List[str]]
    span_links: NotRequired[List[Dict[str, str]]]
    _dd: Dict[str, str]


class LLMObsEvaluationMetricEvent(TypedDict, total=False):
    join_on: Dict[str, Dict[str, str]]
    metric_type: str
    label: str
    categorical_value: str
    numerical_value: float
    score_value: float
    boolean_value: bool
    ml_app: str
    timestamp_ms: int
    tags: List[str]


class LLMObsExperimentEvalMetricEvent(TypedDict, total=False):
    span_id: str
    trace_id: str
    timestamp_ms: int
    metric_type: str
    label: str
    categorical_value: str
    score_value: float
    boolean_value: bool
    error: Optional[Dict[str, str]]
    tags: List[str]
    experiment_id: str


def should_use_agentless(user_defined_agentless_enabled: Optional[bool] = None) -> bool:
    """Determine whether to use agentless mode based on agent availability and capabilities."""
    if user_defined_agentless_enabled is not None:
        return user_defined_agentless_enabled

    agent_info: Optional[Dict[str, Any]]

    try:
        agent_info = agent.info()
    except Exception:
        agent_info = None

    if agent_info is None:
        return True

    endpoints = agent_info.get("endpoints", [])
    return not any(EVP_PROXY_AGENT_BASE_PATH in endpoint for endpoint in endpoints)


class BaseLLMObsWriter(PeriodicService):
    """Base writer class for submitting data to Datadog LLMObs endpoints."""

    RETRY_ATTEMPTS = 3
    BUFFER_LIMIT = 1000
    EVENT_TYPE = ""
    EVP_SUBDOMAIN_HEADER_VALUE = ""
    AGENTLESS_BASE_URL = ""
    ENDPOINT = ""

    def __init__(
        self,
        interval: float,
        timeout: float,
        is_agentless: bool,
        _site: str = "",
        _api_key: str = "",
        _app_key: str = "",
        _override_url: str = "",
    ) -> None:
        super(BaseLLMObsWriter, self).__init__(interval=interval)
        self._lock = forksafe.RLock()
        self._buffer: List[Union[LLMObsSpanEvent, LLMObsEvaluationMetricEvent]] = []
        self._buffer_size: int = 0
        self._timeout: float = timeout
        self._api_key: str = _api_key or config._dd_api_key
        self._site: str = _site or config._dd_site
        self._app_key: str = _app_key
        self._override_url: str = _override_url or os.environ.get("DD_LLMOBS_OVERRIDE_ORIGIN", "")

        self._agentless: bool = is_agentless
        self._intake: str = self._override_url or (
            f"{self.AGENTLESS_BASE_URL}.{self._site}" if is_agentless else agent_config.trace_agent_url
        )
        self._endpoint: str = self.ENDPOINT if is_agentless else f"{EVP_PROXY_AGENT_BASE_PATH}{self.ENDPOINT}"
        self._headers: Dict[str, str] = {"Content-Type": "application/json"}
        if is_agentless:
            self._headers["DD-API-KEY"] = self._api_key
            if self._app_key:
                self._headers["DD-APPLICATION-KEY"] = self._app_key
        else:
            self._headers[EVP_SUBDOMAIN_HEADER_NAME] = self.EVP_SUBDOMAIN_HEADER_VALUE

        self._send_payload_with_retry = fibonacci_backoff_with_jitter(
            attempts=self.RETRY_ATTEMPTS,
            initial_wait=0.618 * self.interval / (1.618**self.RETRY_ATTEMPTS) / 2,
            until=lambda result: isinstance(result, Response),
        )(self._send_payload)

    def start(self, *args, **kwargs):
        super(BaseLLMObsWriter, self).start()
        logger.debug("started %r to %r", self.__class__.__name__, self._url)
        atexit.register(self.on_shutdown)

    def stop(self, timeout=None):
        super(BaseLLMObsWriter, self).stop(timeout=timeout)
        logger.debug("stopped %r to %r", self.__class__.__name__, self._url)
        atexit.unregister(self.on_shutdown)

    def on_shutdown(self):
        self.periodic()

    def _enqueue(self, event: Union[LLMObsSpanEvent, LLMObsEvaluationMetricEvent], event_size: int) -> None:
        """Internal shared logic of enqueuing events to be submitted to LLM Observability."""
        with self._lock:
            if len(self._buffer) >= self.BUFFER_LIMIT:
                logger.warning(
                    "%r event buffer full (limit is %d), dropping event", self.__class__.__name__, self.BUFFER_LIMIT
                )
                telemetry.record_dropped_payload(1, event_type=self.EVENT_TYPE, error="buffer_full")
                return
            if self._buffer_size + event_size > EVP_PAYLOAD_SIZE_LIMIT:
                logger.debug("manually flushing buffer because queueing next event will exceed EVP payload limit")
                self.periodic()
            self._buffer.append(event)
            self._buffer_size += event_size

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
            self._buffer_size = 0

        if self._agentless and not self._headers.get("DD-API-KEY"):
            logger.warning(
                "A Datadog API key is required for sending data to LLM Observability in agentless mode. "
                "LLM Observability data will not be sent. Ensure an API key is set either via DD_API_KEY or via "
                "`LLMObs.enable(api_key=...)` before running your application."
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
        conn = get_connection(self._intake)
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
            return Response.from_http_response(resp)
        except Exception:
            logger.error(
                "failed to send %d LLMObs %s events to %s", num_events, self.EVENT_TYPE, self._intake, exc_info=True
            )
            raise
        finally:
            conn.close()

    @property
    def _url(self) -> str:
        return f"{self._intake}{self._endpoint}"

    def _data(self, events):
        """Return payload containing events to be encoded and submitted to LLM Observability."""
        raise NotImplementedError

    def recreate(self):
        return self.__class__(
            interval=self._interval,
            timeout=self._timeout,
            is_agentless=self._agentless,
            _site=self._site,
            _api_key=self._api_key,
            _override_url=self._override_url,
        )


class LLMObsEvalMetricWriter(BaseLLMObsWriter):
    """Writes custom evaluation metric events to the LLMObs Evals Endpoint."""

    EVENT_TYPE = "evaluation_metric"
    EVP_SUBDOMAIN_HEADER_VALUE = EVAL_SUBDOMAIN_NAME
    AGENTLESS_BASE_URL = AGENTLESS_EVAL_BASE_URL
    ENDPOINT = EVAL_ENDPOINT

    def enqueue(self, event: LLMObsEvaluationMetricEvent) -> None:
        event_size = len(safe_json(event))
        self._enqueue(event, event_size)

    def _data(self, events: List[LLMObsEvaluationMetricEvent]) -> Dict[str, Any]:
        return {"data": {"type": "evaluation_metric", "attributes": {"metrics": events}}}


class LLMObsExperimentsClient(BaseLLMObsWriter):
    EVENT_TYPE = "experiment"
    EVP_SUBDOMAIN_HEADER_VALUE = EXP_SUBDOMAIN_NAME
    AGENTLESS_BASE_URL = AGENTLESS_EXP_BASE_URL
    ENDPOINT = ""
    TIMEOUT = 5.0

    def request(self, method: str, path: str, body: JSONType = None) -> Response:
        headers = {
            "Content-Type": "application/json",
            "DD-API-KEY": self._api_key,
            "DD-APPLICATION-KEY": self._app_key,
        }
        if not self._agentless:
            headers[EVP_SUBDOMAIN_HEADER_NAME] = self.EVP_SUBDOMAIN_HEADER_VALUE

        encoded_body = json.dumps(body).encode("utf-8") if body else b""
        conn = get_connection(url=self._intake, timeout=self.TIMEOUT)
        try:
            url = self._intake + self._endpoint + path
            logger.debug("requesting %s", url)
            conn.request(method, url, encoded_body, headers)
            resp = conn.getresponse()
            return Response.from_http_response(resp)
        finally:
            conn.close()

    def dataset_delete(self, dataset_id: str) -> None:
        path = "/api/unstable/llm-obs/v1/datasets/delete"
        resp = self.request(
            "POST",
            path,
            body={
                "data": {
                    "type": "datasets",
                    "attributes": {
                        "type": "soft",
                        "dataset_ids": [dataset_id],
                    },
                },
            },
        )
        if resp.status != 200:
            raise ValueError(f"Failed to delete dataset {id}: {resp.get_json()}")
        return None

    def dataset_create(self, name: str, description: str) -> Dataset:
        path = "/api/unstable/llm-obs/v1/datasets"
        body: JSONType = {
            "data": {
                "type": "datasets",
                "attributes": {"name": name, "description": description},
            }
        }
        resp = self.request("POST", path, body)
        if resp.status != 200:
            raise ValueError(f"Failed to create dataset {name}: {resp.status} {resp.get_json()}")
        response_data = resp.get_json()
        dataset_id = response_data["data"]["id"]
        curr_version = response_data["data"]["attributes"]["current_version"]
        return Dataset(name, dataset_id, [], description, curr_version, _dne_client=self)

    @staticmethod
    def _get_record_json(record: Union[UpdatableDatasetRecord, DatasetRecordRaw], is_update: bool) -> JSONType:
        # for now, if a user wants to "erase" the value of expected_output, they are expected to
        # set expected_output to None, and we serialize that as empty string to indicate this to BE
        expected_output: JSONType = None
        if "expected_output" in record:
            expected_output = "" if record["expected_output"] is None else record["expected_output"]

        # for now, if a user wants to "erase" the value of metadata, they are expected to
        # set metadata to None, and we serialize that as an empty map to indicate this to BE
        metadata: JSONType = None
        if "metadata" in record:
            metadata = {} if record["metadata"] is None else record["metadata"]

        rj: JSONType = {
            "input": cast(Dict[str, JSONType], record.get("input_data")),
            "expected_output": expected_output,
            "metadata": metadata,
        }

        if is_update:
            rj["id"] = record["record_id"]  # type: ignore

        return rj

    def dataset_batch_update(
        self,
        dataset_id: str,
        insert_records: List[DatasetRecordRaw],
        update_records: List[UpdatableDatasetRecord],
        delete_record_ids: List[str],
    ) -> Tuple[int, List[str]]:
        irs: JSONType = [self._get_record_json(r, False) for r in insert_records]
        urs: JSONType = [self._get_record_json(r, True) for r in update_records]
        path = f"/api/unstable/llm-obs/v1/datasets/{dataset_id}/batch_update"
        body: JSONType = {
            "data": {
                "type": "datasets",
                "id": dataset_id,
                "attributes": {
                    "insert_records": irs,
                    "update_records": urs,
                    "delete_records": cast(JSONType, delete_record_ids),  # mypy bug?
                },
            }
        }
        resp = self.request("POST", path, body)
        if resp.status != 200:
            raise ValueError(f"Failed to update dataset {dataset_id}: {resp.status}")  # nosec
        response_data = resp.get_json()
        data = response_data["data"]

        # FIXME: we don't get version numbers in responses to deletion requests
        new_version = data[0]["attributes"]["version"] if data else -1
        new_record_ids: List[str] = [r["id"] for r in data] if data else []
        return new_version, new_record_ids

    def dataset_get_with_records(self, name: str) -> Dataset:
        path = f"/api/unstable/llm-obs/v1/datasets?filter[name]={quote(name)}"
        resp = self.request("GET", path)
        if resp.status != 200:
            raise ValueError(f"Failed to pull dataset {name}: {resp.status}")

        response_data = resp.get_json()
        data = response_data["data"]
        if not data:
            raise ValueError(f"Dataset '{name}' not found")

        curr_version = data[0]["attributes"]["current_version"]
        dataset_description = data[0]["attributes"].get("description", "")
        dataset_id = data[0]["id"]

        path = f"/api/unstable/llm-obs/v1/datasets/{dataset_id}/records"
        resp = self.request("GET", path)
        if resp.status != 200:
            raise ValueError(f"Failed to pull dataset {name}: {resp.status} {resp.get_json()}")
        records_data = resp.get_json()

        class_records: List[DatasetRecord] = []
        for record in records_data.get("data", []):
            attrs = record.get("attributes", {})
            class_records.append(
                {
                    "record_id": record["id"],
                    "input_data": attrs["input"],
                    "expected_output": attrs.get("expected_output"),
                    "metadata": attrs.get("metadata", {}),
                }
            )
        return Dataset(name, dataset_id, class_records, dataset_description, curr_version, _dne_client=self)

    def project_create_or_get(self, name: str) -> str:
        path = "/api/unstable/llm-obs/v1/projects"
        resp = self.request(
            "POST",
            path,
            body={"data": {"type": "projects", "attributes": {"name": name, "description": ""}}},
        )
        if resp.status != 200:
            raise ValueError(f"Failed to create project {name}: {resp.status} {resp.get_json()}")
        response_data = resp.get_json()
        return response_data["data"]["id"]

    def experiment_create(
        self,
        name: str,
        dataset_id: str,
        project_id: str,
        dataset_version: int = 0,
        exp_config: Optional[Dict[str, JSONType]] = None,
        tags: Optional[List[str]] = None,
        description: Optional[str] = None,
    ) -> Tuple[str, str]:
        path = "/api/unstable/llm-obs/v1/experiments"
        resp = self.request(
            "POST",
            path,
            body={
                "data": {
                    "type": "experiments",
                    "attributes": {
                        "name": name,
                        "description": description or "",
                        "dataset_id": dataset_id,
                        "project_id": project_id,
                        "dataset_version": dataset_version,
                        "config": exp_config or {},
                        "metadata": {"tags": cast(JSONType, tags or [])},
                        "ensure_unique": True,
                    },
                }
            },
        )
        if resp.status != 200:
            raise ValueError(f"Failed to create experiment {name}: {resp.status} {resp.get_json()}")
        response_data = resp.get_json()
        experiment_id = response_data["data"]["id"]
        experiment_run_name = response_data["data"]["attributes"]["name"]  # API calls run-name as name
        return experiment_id, experiment_run_name

    def experiment_eval_post(
        self, experiment_id: str, events: List[LLMObsExperimentEvalMetricEvent], tags: List[str]
    ) -> None:
        path = f"/api/unstable/llm-obs/v1/experiments/{experiment_id}/events"
        resp = self.request(
            "POST",
            path,
            body={
                "data": {
                    "type": "experiments",
                    "attributes": {
                        "scope": "experiments",
                        "metrics": cast(List[JSONType], events),
                        "tags": cast(List[JSONType], tags),
                    },
                }
            },
        )
        if resp.status not in (200, 202):
            raise ValueError(
                f"Failed to post experiment evaluation metrics for {experiment_id}: {resp.status} {resp.get_json()}"
            )
        logger.debug("Sent %d experiment evaluation metrics for %s", len(events), experiment_id)
        return None


class LLMObsSpanWriter(BaseLLMObsWriter):
    """Writes span events to the LLMObs Span Endpoint."""

    EVENT_TYPE = "span"
    EVP_SUBDOMAIN_HEADER_VALUE = SPAN_SUBDOMAIN_NAME
    AGENTLESS_BASE_URL = AGENTLESS_SPAN_BASE_URL
    ENDPOINT = SPAN_ENDPOINT

    def enqueue(self, event: LLMObsSpanEvent) -> None:
        raw_event_size = len(safe_json(event))
        truncated_event_size = None
        should_truncate = raw_event_size >= EVP_EVENT_SIZE_LIMIT
        if should_truncate:
            logger.warning(
                "dropping event input/output because its size (%d) exceeds the event size limit (5MB)",
                raw_event_size,
            )
            event = _truncate_span_event(event)
            truncated_event_size = len(safe_json(event))
        telemetry.record_span_event_raw_size(event, raw_event_size)
        telemetry.record_span_event_size(event, truncated_event_size or raw_event_size)
        self._enqueue(event, truncated_event_size or raw_event_size)

    def _data(self, events: List[LLMObsSpanEvent]) -> List[Dict[str, Any]]:
        payload = []
        for event in events:
            event_data = {
                "_dd.stage": "raw",
                "_dd.tracer_version": ddtrace.__version__,
                "event_type": "span",
                "spans": [event],
            }
            if event.get("_dd", {}).get("scope") == "experiments":
                event_data["_dd.scope"] = "experiments"
            payload.append(event_data)
        return payload


def _truncate_span_event(event: LLMObsSpanEvent) -> LLMObsSpanEvent:
    event["meta"]["input"] = {"value": DROPPED_VALUE_TEXT}
    event["meta"]["output"] = {"value": DROPPED_VALUE_TEXT}

    event["collection_errors"] = [DROPPED_IO_COLLECTION_ERROR]
    return event
