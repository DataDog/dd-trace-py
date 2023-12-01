import atexit
import json
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


class LLMObsEvent(TypedDict):
    """
    Note: these attribute names match the corresponding entry in the JSON payload.
    """

    timestamp: int
    id: str
    type: str
    input: Dict[str, Union[float, int, List[str]]]
    model: str
    model_provider: str
    output: Dict[str, List[Dict[str, str]]]
    # Additional attributes can be specified on the event
    # including dd.trace_id and dd.span_id to correlate a trace


class LLMObsWriter(PeriodicService):
    """Writer to the Datadog LLMObs intake."""

    def __init__(self, site, api_key, app_key, interval, timeout):
        # type: (str, str, str, float, float) -> None
        super(LLMObsWriter, self).__init__(interval=interval)
        self._lock = forksafe.RLock()
        self._buffer = []  # type: List[LLMObsEvent]
        # match the API limit
        self._buffer_limit = 1000
        self._timeout = timeout  # type: float
        self._api_key = api_key or ""  # type: str
        self._app_key = app_key or ""  # type: str
        self._endpoint = "/api/unstable/llm-obs/v1/records"  # type: str
        self._site = site  # type: str
        self._intake = "api.%s" % self._site  # type: str
        self._headers = {
            "DD-API-KEY": self._api_key,
            "DD-APPLICATION-KEY": self._app_key,
            "Content-Type": "application/json",
        }

    def start(self, *args, **kwargs):
        super(LLMObsWriter, self).start()
        logger.debug("started llmobs writer to %r", self._url)
        atexit.register(self.on_shutdown)

    def enqueue(self, log):
        # type: (LLMObsEvent) -> None
        with self._lock:
            if len(self._buffer) >= self._buffer_limit:
                logger.warning("LLMobs record buffer full (limit is %d), dropping record", self._buffer_limit)
                return
            self._buffer.append(log)

    def on_shutdown(self):
        # TODO: Once we submit to the public EVP endpoint which accepts record-level model/model_provider
        #  fields instead of a single model/model_provider for the whole payload,
        #  we can remove this loop and just send the whole buffer at once in one periodic() call
        while self._buffer:
            self.periodic()

    @property
    def _url(self):
        # type: () -> str
        return "https://%s%s" % (self._intake, self._endpoint)

    def periodic(self):
        # type: () -> None
        with self._lock:
            if not self._buffer:
                return
            llm_records = [self._buffer.pop()]
            # This is a workaround the fact that the record ingest API only accepts a single model/model_provider
            #  for the whole payload, so we need to send all records with the same model/model_provider together
            while self._buffer:
                record = self._buffer[0]
                if record["model"] != llm_records[0]["model"]:
                    break
                if record["model_provider"] != llm_records[0]["model_provider"]:
                    break
                llm_records.append(self._buffer.pop())

        model = llm_records[0]["model"]
        model_provider = llm_records[0]["model_provider"]
        for record in llm_records:
            record.pop("model", None)  # type: ignore[misc]
            record.pop("model_provider", None)  # type: ignore[misc]
        data = {
            "data": {
                "type": "records",
                "attributes": {
                    "tags": ["src:integration"],
                    "model": model,
                    "model_provider": model_provider,
                    "records": llm_records,
                },
            }
        }
        num_llm_records = len(llm_records)
        try:
            enc_llm_records = json.dumps(data)
        except TypeError:
            logger.error("failed to encode %d LLM records", num_llm_records, exc_info=True)
            return

        conn = httplib.HTTPSConnection(self._intake, 443, timeout=self._timeout)
        try:
            conn.request("POST", self._endpoint, enc_llm_records, self._headers)
            resp = get_connection_response(conn)
            if resp.status >= 300:
                logger.error(
                    "failed to send %d LLM records to %r, got response code %r, status: %r",
                    num_llm_records,
                    self._url,
                    resp.status,
                    resp.read(),
                )
            else:
                logger.debug("sent %d LLM records to %r", num_llm_records, self._url)
        except Exception:
            logger.error("failed to send %d LLM records to %r", num_llm_records, self._intake, exc_info=True)
        finally:
            conn.close()
