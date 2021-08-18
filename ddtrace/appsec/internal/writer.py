from collections import defaultdict
import json
import logging
from typing import Iterable
from typing import Optional
from typing import TYPE_CHECKING

import attr
import tenacity

from ddtrace.appsec.internal.buffer import Buffer
from ddtrace.appsec.internal.buffer import BufferFull
from ddtrace.appsec.internal.buffer import BufferItemTooLarge
from ddtrace.appsec.internal.events import Event
from ddtrace.internal import _rand
from ddtrace.internal import agent
from ddtrace.internal import atexit
from ddtrace.internal import compat
from ddtrace.internal import forksafe
from ddtrace.internal import periodic
from ddtrace.internal import service
from ddtrace.utils.time import StopWatch


if TYPE_CHECKING:
    from ddtrace.internal.dogstatsd import DogStatsd

log = logging.getLogger(__name__)

DEFAULT_URL = agent.get_trace_url() + "/appsec/proxy/"
DEFAULT_TIMEOUT = 2.0  # seconds
DEFAULT_RETRY_ATTEMPS = 3
DEFAULT_MAX_PAYLOAD_SIZE = 4 * 1024 * 1024  # b
DEFAULT_FLUSH_INTERVAL = 3.0  # seconds


class BaseEventWriter(object):
    def write(self, events):
        # type: (Iterable[Event]) -> None
        """Queue AppSec events to the writer."""
        raise NotImplementedError

    def flush(self, timeout=None):
        # type: (Optional[float]) -> None
        """Send events and wait for completion."""
        raise NotImplementedError


class NullEventWriter(BaseEventWriter):
    def write(self, events):
        # type: (Iterable[Event]) -> None
        pass

    def flush(self, timeout=None):
        # type: (Optional[float]) -> None
        pass


class HTTPEventEncoderV1(Buffer):

    content_type = "application/json"

    def put_item(self, item):
        # type: (Event) -> None
        return self.put(json.dumps(attr.asdict(item), separators=(",", ":")).encode("ascii"))

    def encode(self):
        # type: () -> Optional[bytes]
        data = self.get()
        if data:
            return (
                b'{"protocol_version":1,"idempotency_key":"'
                + str(_rand.rand64bits()).encode("ascii")
                + b'","events": ['
                + b",".join(data)
                + b"]}"
            )
        return None


class HTTPRequestFailed(Exception):
    """
    Exception raised when the events payload cannot be sent.
    """


class HTTPEventWriter(periodic.PeriodicService, BaseEventWriter):
    """
    Batch events to an HTTP endpoint using AppSec intake protocol.
    """

    def __init__(
        self,
        url=DEFAULT_URL,  # type: str
        api_key=None,  # type: Optional[str]
        timeout=DEFAULT_TIMEOUT,  # type: float
        retry_attemps=DEFAULT_RETRY_ATTEMPS,  # type: int
        max_payload_size=DEFAULT_MAX_PAYLOAD_SIZE,  # type: int
        flush_interval=DEFAULT_FLUSH_INTERVAL,  # type: float
        dogstatsd=None,  # type: Optional[DogStatsd]
    ):
        # type: (...) -> None
        super(HTTPEventWriter, self).__init__(interval=flush_interval)
        self._retry_upload = tenacity.Retrying(
            wait=tenacity.wait_random_exponential(multiplier=0.5),
            stop=tenacity.stop_after_attempt(retry_attemps),
            retry=tenacity.retry_if_exception_type((compat.httplib.HTTPException, OSError, IOError)),
        )
        self.url = url
        self.api_key = api_key
        self._conn = agent.get_connection(url, timeout=timeout)
        self._encoder = HTTPEventEncoderV1(max_size=max_payload_size, max_item_size=max_payload_size)
        self._headers = {"Content-Type": self._encoder.content_type}
        if api_key is not None:
            self._headers["DD-API-KEY"] = api_key
        self._dogstatsd = dogstatsd
        self._metrics_reset()

    def _metrics_dist(self, name, count=1, tags=None):
        self._metrics[name]["count"] += count
        if tags:
            self._metrics[name]["tags"].extend(tags)

    def _metrics_reset(self):
        self._metrics = defaultdict(lambda: {"count": 0, "tags": []})

    def write(self, events):
        # type: (Iterable[Event]) -> None
        """
        Queue events in the encoded events buffer.

        The method will silently drop events if the encoded events exceed
        the maximum payload size or if the buffer is full.

        A background thread is started to send the buffer periodically.
        """
        try:
            if self.status != service.ServiceStatus.RUNNING:
                log.debug("starting the HTTPEventWriter")
                self.start()
        except service.ServiceStatusError:
            pass

        for event in events:
            try:
                self._encoder.put_item(event)
            except BufferItemTooLarge as e:
                payload_size = e.args[0]
                log.warning(
                    "AppSec event (%db) larger than payload max size (%db), dropping",
                    payload_size,
                    self._encoder.max_item_size,
                )
                self._metrics_dist("buffer.dropped.events", tags=["reason:too_big"])
                self._metrics_dist("buffer.dropped.bytes", payload_size, tags=["reason:too_big"])
            except BufferFull as e:
                payload_size = e.args[0]
                log.warning(
                    "AppSec buffer (%s events %db/%db) cannot fit event of size %db, dropping",
                    len(self._encoder),
                    self._encoder.size,
                    self._encoder.max_size,
                    payload_size,
                )
                self._metrics_dist("buffer.dropped.events", tags=["reason:full"])
                self._metrics_dist("buffer.dropped.bytes", payload_size, tags=["reason:full"])
            except Exception:
                log.error("failed to encode event %r", event, exc_info=True)
                self._metrics_dist("buffer.dropped.events", tags=["reason:encoding"])
            else:
                self._metrics_dist("buffer.accepted.events")

    def flush(self, timeout=None):
        # type: (Optional[float]) -> None
        """
        Flush the encoded events buffer and wait for completion
        up to the specified timeout argument.

        The background thread is stopped afterwards.
        """
        if timeout is None:
            timeout = DEFAULT_TIMEOUT
        try:
            self.stop()
        except service.ServiceStatusError:
            pass
        else:
            self.join(timeout)

    def _start_service(self, *args, **kwargs):
        super(HTTPEventWriter, self)._start_service(*args, **kwargs)
        atexit.register(self.flush)
        forksafe.register(self._reset)

    def _stop_service(self, *args, **kwargs):
        atexit.unregister(self.flush)
        forksafe.unregister(self._reset)
        super(HTTPEventWriter, self)._stop_service(*args, **kwargs)

    def _reset(self):
        # clear the buffer and metrics to prevent stale data to be sent
        self._encoder.get()
        self._metrics_reset()
        # stop the periodic thread if it runs, ignore otherwise
        try:
            self.stop()
        except service.ServiceStatusError:
            pass

    def _send_payload(self, payload):
        self._metrics_dist("http.requests")
        with StopWatch() as sw:
            try:
                self._conn.request("POST", "v1/input", payload, self._headers)
                resp = self._conn.getresponse()
                # read the response body otherwise we can't reuse the connection
                # even if we don't use it.
                resp.read()
            except Exception:
                # reset the connection in case of error
                self._conn.close()
                raise

        t = sw.elapsed()
        if t >= self.interval:
            log_level = logging.WARNING
        else:
            log_level = logging.DEBUG
        log.log(log_level, "sent %db in %.5fs to %s", len(payload), t, self.url)

        if resp.status >= 400:
            self._metrics_dist("http.errors", tags=["type:{}".format(resp.status)])

        if resp.status >= 500:
            raise tenacity.TryAgain

        if resp.status >= 400:
            if resp.status == 404:
                log.warning("Datadog agent refuses AppSec events. Please install the latest version.")
            elif resp.status == 405 and self.api_key is None:
                log.warning("Datadog agent refuses AppSec events. Please enable it with DD_APPSEC_ENABLED=true.")
            else:
                log.error("failed to send AppSec events: HTTP error status %s, reason %s", resp.status, resp.reason)
            raise HTTPRequestFailed

    def _flush_events(self):
        try:
            size = self._encoder.size
            n_events = len(self._encoder)
            try:
                payload = self._encoder.encode()
                if payload is None:
                    return
            except Exception:
                log.error("failed to encode events", exc_info=True)
                self._metrics_dist("buffer.dropped.events", n_events, tags=["reason:dto"])
                self._metrics_dist("buffer.dropped.bytes", size, tags=["reason:dto"])
                return

            try:
                self._retry_upload(self._send_payload, payload)
            except tenacity.RetryError:
                log.error("failed to send AppSec events", exc_info=True)
                self._metrics_dist("http.errors", count=0, tags=["type:err"])
                self._metrics_dist("http.dropped.events", n_events)
                self._metrics_dist("http.dropped.bytes", len(payload))
            except HTTPRequestFailed:
                self._metrics_dist("http.errors", count=0, tags=["type:err"])
                self._metrics_dist("http.dropped.events", n_events)
                self._metrics_dist("http.dropped.bytes", len(payload))
            else:
                self._metrics_dist("http.sent.events", n_events)
                self._metrics_dist("http.sent.bytes", len(payload))
            finally:
                if self._dogstatsd is not None:
                    for name, metric in self._metrics.items():
                        self._dogstatsd.distribution(
                            "datadog.tracer.appsec.{}".format(name), metric["count"], tags=metric["tags"]
                        )
        finally:
            self._metrics_reset()

    def periodic(self):
        self._flush_events()

    def on_shutdown(self):
        self._flush_events()
        try:
            self._conn.close()
        except Exception:
            pass
