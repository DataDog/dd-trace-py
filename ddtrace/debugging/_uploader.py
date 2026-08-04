from dataclasses import dataclass
from enum import Enum
from typing import Any
from typing import Optional

from ddtrace.debugging._config import di_config
from ddtrace.debugging._encoding import LogSignalJsonEncoder
from ddtrace.debugging._encoding import SignalQueue
from ddtrace.debugging._encoding import SnapshotJsonEncoder
from ddtrace.debugging._metrics import metrics
from ddtrace.debugging._signal.collector import SignalCollector
from ddtrace.debugging._signal.model import SignalTrack
from ddtrace.internal import agent
from ddtrace.internal import logger
from ddtrace.internal.debugger_sender import build_debugger_sender
from ddtrace.internal.logger import get_logger
from ddtrace.internal.native import DebuggerType
from ddtrace.internal.utils.retry import fibonacci_backoff_with_jitter


log = get_logger(__name__)
UNSUPPORTED_AGENT = "unsupported_agent"
logger.set_tag_rate_limit(UNSUPPORTED_AGENT, logger.HOUR)


meter = metrics.get_meter("uploader")


class UploaderProduct(str, Enum):
    """Uploader products."""

    DEBUGGER = "dynamic_instrumentation"
    EXCEPTION_REPLAY = "exception_replay"
    CODE_ORIGIN_SPAN_ENTRY = "code_origin.span.entry"


@dataclass
class UploaderTrack:
    track: SignalTrack
    debugger_type: DebuggerType
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
    _products: set[UploaderProduct] = set()
    _agent_endpoints: set[str] = set()

    __queue__ = SignalQueue
    __collector__ = SignalCollector

    RETRY_ATTEMPTS = 3

    def __init__(self, interval: Optional[float] = None) -> None:
        super().__init__(interval if interval is not None else di_config.upload_interval_seconds)

        self._sender = build_debugger_sender()

        self._tracks = {
            SignalTrack.LOGS: UploaderTrack(
                track=SignalTrack.LOGS,
                debugger_type=DebuggerType.Logs,
                queue=self.__queue__(
                    encoder=LogSignalJsonEncoder(di_config.service_name), on_full=self._on_buffer_full
                ),
            ),
            SignalTrack.SNAPSHOT: UploaderTrack(
                track=SignalTrack.SNAPSHOT,
                debugger_type=DebuggerType.Snapshots,
                queue=self.__queue__(encoder=SnapshotJsonEncoder(di_config.service_name), on_full=self._on_buffer_full),
            ),
        }
        self._collector = self.__collector__({t: ut.queue for t, ut in self._tracks.items()})

        if self._sender.agentless:
            # There is no agent to negotiate endpoints with, so skip the agent
            # check state and start uploading straight away.
            self._state = self._online

        # Make it retry-able
        self._write_with_backoff = fibonacci_backoff_with_jitter(
            initial_wait=0.618 * self.interval / (1.618**self.RETRY_ATTEMPTS) / 2,
            attempts=self.RETRY_ATTEMPTS,
        )(self._write)

        log.debug("Signal uploader initialized (sender: %r, interval: %f)", self._sender, self.interval)

        self._flush_full = False

    def info_check(self, agent_info: Optional[dict[str, Any]]) -> bool:
        if self._sender.agentless:
            # Payloads go straight to the intake, on paths that are fixed at
            # construction: there is nothing to negotiate with the agent.
            return True

        if agent_info is None:
            # Agent is unreachable
            return False

        if "endpoints" not in agent_info:
            # Agent not supported
            log.debug("Unsupported Datadog agent detected. Please upgrade to 7.49.0.")
            return False

        # Agent /info entries may or may not carry a leading slash depending on
        # the agent version, so normalize before matching (see remoteconfig).
        endpoints = {endpoint.lstrip("/") for endpoint in agent_info.get("endpoints", [])}

        logs_track = self._tracks[SignalTrack.LOGS]
        snapshot_track = self._tracks[SignalTrack.SNAPSHOT]
        logs_track.enabled = True
        snapshot_track.enabled = True

        if "debugger/v2/input" in endpoints:
            log.debug("Detected /debugger/v2/input endpoint")
            # Undo any downgrade from an earlier online cycle.
            self._sender.reset_endpoints()
        elif "debugger/v1/diagnostics" in endpoints:
            log.debug("Detected /debugger/v1/diagnostics endpoint fallback")
            self._sender.downgrade_to_diagnostics()
        else:
            logs_track.enabled = False
            snapshot_track.enabled = False
            self._throttle_agent_check()
            log.warning(
                UNSUPPORTED_AGENT,
                extra={
                    "product": "debugger",
                    "more_info": (
                        "Unsupported Datadog agent detected. Logs and snapshots from Dynamic Instrumentation/"
                        "Exception Replay/Code Origin for Spans will not be uploaded. "
                        "Please upgrade to version 7.49.0 or later"
                    ),
                },
            )

        return True

    def _write(self, payload: bytes, debugger_type: DebuggerType) -> None:
        try:
            rejected = self._sender.send(payload, debugger_type)
        except Exception:
            # The request never completed (transport failure or timeout). Drop the
            # batch: unlike a rejection, there is no endpoint to fall back to.
            log.error("Failed to write payload to the %s track", debugger_type, exc_info=True)
            meter.increment("error")
            return

        if rejected is not None:
            status, body = rejected
            log.error("Failed to upload payload to the %s track: [%d] %r", debugger_type, status, body)
            meter.increment("upload.error", tags={"status": str(status)})
            msg = "Failed to upload payload"
            raise SignalUploaderError(msg)

        meter.increment("upload.success")
        meter.distribution("upload.size", len(payload))

    def _on_buffer_full(self, _item: Any, _encoded: bytes) -> None:
        self._flush_full = True
        self.upload()

    def upload(self) -> None:
        """Upload request."""
        self.awake()

    def reset(self) -> None:
        """Reset the buffer on fork."""
        super().reset()
        for track in self._tracks.values():
            track.queue = self.__queue__(encoder=track.queue._encoder, on_full=self._on_buffer_full)
        self._collector._tracks = {t: ut.queue for t, ut in self._tracks.items()}

    def _downgrade_to_diagnostics(self) -> bool:
        """Downgrade the logs and snapshots tracks to the diagnostics endpoint.

        Returns whether the downgrade happened; it does not when the tracks are
        already downgraded, or in agentless mode where all tracks share one path.
        """
        if not self._sender.downgrade_to_diagnostics():
            return False

        log.debug("Downgraded debugger endpoints to the diagnostics endpoint")

        return True

    def _flush_track(self, track: UploaderTrack) -> None:
        if (data := track.queue.flush()) is not None and track.enabled:
            payload, count = data
            try:
                self._write_with_backoff(payload, track.debugger_type)
                meter.distribution("batch.cardinality", count)
            except SignalUploaderError:
                if self._downgrade_to_diagnostics():
                    # Retry once against the diagnostics endpoint
                    self._write_with_backoff(payload, track.debugger_type)
                    meter.distribution("batch.cardinality", count)
                elif self._sender.agentless:
                    log.debug("Cannot upload payload to the intake", exc_info=True)
                else:
                    raise  # Propagate error to transition to agent check state
            except Exception:
                log.debug("Cannot upload payload", exc_info=True)

    def _flush(self) -> None:
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

    def online(self) -> None:
        self._flush()

        if not self._tracks[SignalTrack.SNAPSHOT].enabled or not self._tracks[SignalTrack.LOGS].enabled:
            # If the tracks are not enabled, we raise an exception to
            # transition back to the agent check state in case we detect an
            # agent that can handle logs and snapshots safely.
            msg = "Debugger tracks not enabled"
            raise ValueError(msg)

    def on_shutdown(self) -> None:  # type: ignore[override]
        self._flush()

    @classmethod
    def get_collector(cls) -> Optional[SignalCollector]:
        return cls._instance._collector if cls._instance is not None else None

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
