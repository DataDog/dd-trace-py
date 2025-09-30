from dataclasses import dataclass
from enum import Enum
from typing import Any
from typing import Optional
from typing import Set
from urllib.parse import quote

from ddtrace.debugging._config import di_config
from ddtrace.debugging._encoding import LogSignalJsonEncoder
from ddtrace.debugging._encoding import SignalQueue
from ddtrace.debugging._encoding import SnapshotJsonEncoder
from ddtrace.debugging._metrics import metrics
from ddtrace.debugging._signal.collector import SignalCollector
from ddtrace.debugging._signal.model import SignalTrack
from ddtrace.internal import agent
from ddtrace.internal import logger
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.http import connector
from ddtrace.internal.utils.retry import fibonacci_backoff_with_jitter


log = get_logger(__name__)
UNSUPPORTED_AGENT = "unsupported_agent"
logger.set_tag_rate_limit(UNSUPPORTED_AGENT, logger.HOUR)


meter = metrics.get_meter("uploader")


class UploaderProduct(str, Enum):
    """Uploader products."""

    DEBUGGER = "dynamic_instrumentation"
    EXCEPTION_REPLAY = "exception_replay"
    CODE_ORIGIN_SPAN = "code_origin.span"


@dataclass
class UploaderTrack:
    track: SignalTrack
    endpoint: str
    queue: SignalQueue
    enabled: bool = True


class SignalUploaderError(Exception):
    """Signal uploader error."""

    pass


class SignalUploader(agent.AgentCheckPeriodicService):
    """Signal uploader.

    This class implements an interface with the debugger signal intake for both
    the debugger and the events platform.
    """

    _instance: Optional["SignalUploader"] = None
    _products: Set[UploaderProduct] = set()
    _agent_endpoints: Set[str] = set()

    __queue__ = SignalQueue
    __collector__ = SignalCollector

    RETRY_ATTEMPTS = 3

    def __init__(self, interval: Optional[float] = None) -> None:
        super().__init__(interval if interval is not None else di_config.upload_interval_seconds)

        self._endpoint_suffix = endpoint_suffix = (
            f"?ddtags={quote(di_config.tags)}" if di_config._tags_in_qs and di_config.tags else ""
        )

        self._tracks = {
            SignalTrack.LOGS: UploaderTrack(
                track=SignalTrack.LOGS,
                endpoint=f"/debugger/v1/input{endpoint_suffix}",
                queue=self.__queue__(
                    encoder=LogSignalJsonEncoder(di_config.service_name), on_full=self._on_buffer_full
                ),
            ),
            SignalTrack.SNAPSHOT: UploaderTrack(
                track=SignalTrack.SNAPSHOT,
                endpoint=f"/debugger/v2/input{endpoint_suffix}",  # start optimistically
                queue=self.__queue__(encoder=SnapshotJsonEncoder(di_config.service_name), on_full=self._on_buffer_full),
            ),
        }
        self._collector = self.__collector__({t: ut.queue for t, ut in self._tracks.items()})
        self._headers = {
            "Content-type": "application/json; charset=utf-8",
            "Accept": "text/plain",
        }

        self._connect = connector(di_config._intake_url, timeout=di_config.upload_timeout)

        # Make it retry-able
        self._write_with_backoff = fibonacci_backoff_with_jitter(
            initial_wait=0.618 * self.interval / (1.618**self.RETRY_ATTEMPTS) / 2,
            attempts=self.RETRY_ATTEMPTS,
        )(self._write)

        log.debug(
            "Signal uploader initialized (url: %s, endpoints: %s, interval: %f)",
            di_config._intake_url,
            {t: ut.endpoint for t, ut in self._tracks.items()},
            self.interval,
        )

        self._flush_full = False

    def info_check(self, agent_info: Optional[dict]) -> bool:
        if agent_info is None:
            # Agent is unreachable
            return False

        if "endpoints" not in agent_info:
            # Agent not supported
            log.debug("Unsupported Datadog agent detected. Please upgrade to 7.49.0.")
            return False

        endpoints = set(agent_info.get("endpoints", []))
        snapshot_track = self._tracks[SignalTrack.SNAPSHOT]
        snapshot_track.enabled = True

        if "/debugger/v2/input" in endpoints:
            log.debug("Detected /debugger/v2/input endpoint")
            snapshot_track.endpoint = f"/debugger/v2/input{self._endpoint_suffix}"
        elif "/debugger/v1/diagnostics" in endpoints:
            log.debug("Detected /debugger/v1/diagnostics endpoint fallback")
            snapshot_track.endpoint = f"/debugger/v1/diagnostics{self._endpoint_suffix}"
        else:
            snapshot_track.enabled = False
            log.warning(
                UNSUPPORTED_AGENT,
                extra={
                    "product": "debugger",
                    "more_info": (
                        "Unsupported Datadog agent detected. Snapshots from Dynamic Instrumentation/"
                        "Exception Replay/Code Origin for Spans will not be uploaded. "
                        "Please upgrade to version 7.49.0 or later"
                    ),
                },
            )

        return True

    def _write(self, payload: bytes, endpoint: str) -> None:
        try:
            with self._connect() as conn:
                conn.request("POST", endpoint, payload, headers=self._headers)
                resp = conn.getresponse()
                if not (200 <= resp.status < 300):
                    log.error("Failed to upload payload to endpoint %s: [%d] %r", endpoint, resp.status, resp.read())
                    meter.increment("upload.error", tags={"status": str(resp.status)})
                    if 400 <= resp.status < 500:
                        msg = "Failed to upload payload"
                        raise SignalUploaderError(msg)
                else:
                    meter.increment("upload.success")
                    meter.distribution("upload.size", len(payload))
        except SignalUploaderError:
            raise
        except Exception:
            log.error("Failed to write payload to endpoint %s", endpoint, exc_info=True)
            meter.increment("error")

    def _on_buffer_full(self, _item: Any, _encoded: bytes) -> None:
        self._flush_full = True
        self.upload()

    def upload(self) -> None:
        """Upload request."""
        self.awake()

    def reset(self) -> None:
        """Reset the buffer on fork."""
        for track in self._tracks.values():
            track.queue = self.__queue__(encoder=track.queue._encoder, on_full=self._on_buffer_full)
        self._collector._tracks = {t: ut.queue for t, ut in self._tracks.items()}

    def _flush_track(self, track: UploaderTrack) -> None:
        if (data := track.queue.flush()) is not None and track.enabled:
            payload, count = data
            try:
                self._write_with_backoff(payload, track.endpoint)
                meter.distribution("batch.cardinality", count)
            except SignalUploaderError:
                if track.track is SignalTrack.SNAPSHOT and not track.endpoint.startswith("/debugger/v1/diagnostics"):
                    # Downgrade to diagnostics endpoint and retry once
                    track.endpoint = f"/debugger/v1/diagnostics{self._endpoint_suffix}"
                    log.debug("Downgrading snapshot endpoint to %s and trying again", track.endpoint)
                    self._write_with_backoff(payload, track.endpoint)
                    meter.distribution("batch.cardinality", count)
                else:
                    raise  # Propagate error to transition to agent check state
            except Exception:
                log.debug("Cannot upload logs payload", exc_info=True)

    def online(self) -> None:
        """Upload the buffer content to the agent."""
        if self._flush_full:
            # We received the signal to flush a full buffer
            self._flush_full = False
            for uploader_track in self._tracks.values():
                if uploader_track.queue.is_full():
                    self._flush_track(uploader_track)

        for track in self._tracks.values():
            if track.queue.count:
                self._flush_track(track)

        if not self._tracks[SignalTrack.SNAPSHOT].enabled:
            # If the snapshot track is not enabled, we raise an exception to
            # transition back to the agent check state in case we detect an
            # agent that can handle snapshots safely.
            msg = "Snapshot track not enabled"
            raise ValueError(msg)

    on_shutdown = online

    @classmethod
    def get_collector(cls) -> SignalCollector:
        if cls._instance is None:
            raise RuntimeError("No products registered with the uploader")

        return cls._instance._collector

    @classmethod
    def register(cls, product: UploaderProduct) -> None:
        if product in cls._products:
            return

        cls._products.add(product)

        if cls._instance is None:
            cls._instance = cls()
            cls._instance.start()

    @classmethod
    def unregister(cls, product: UploaderProduct) -> None:
        if product not in cls._products:
            return

        cls._products.remove(product)

        if not cls._products and cls._instance is not None:
            cls._instance.stop()
            cls._instance = None
