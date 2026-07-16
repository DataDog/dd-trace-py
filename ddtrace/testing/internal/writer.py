from abc import ABC
from abc import abstractmethod
import base64
import logging
import threading
import typing as t
import uuid

from ddtrace.internal.settings import env
from ddtrace.testing.internal.http import BackendConnectorSetup
from ddtrace.testing.internal.http import FileAttachment
from ddtrace.testing.internal.http import Subdomain
from ddtrace.testing.internal.offline_mode import write_payload_file
from ddtrace.testing.internal.telemetry import TelemetryAPI
from ddtrace.testing.internal.test_data import TestItem
from ddtrace.testing.internal.test_data import TestModule
from ddtrace.testing.internal.test_data import TestRun
from ddtrace.testing.internal.test_data import TestSession
from ddtrace.testing.internal.test_data import TestStatus
from ddtrace.testing.internal.test_data import TestSuite
from ddtrace.testing.internal.tracer_api import StopWatch
from ddtrace.testing.internal.tracer_api import msgpack_packb
from ddtrace.version import __version__


log = logging.getLogger(__name__)


Event = dict[str, t.Any]

TSerializable = t.TypeVar("TSerializable", bound=TestItem[t.Any, t.Any])

EventSerializer = t.Callable[[TSerializable], Event]

_MAX_META_TAG_VALUE_LENGTH = 5000


def _truncate_meta_string_values(meta: dict[str, t.Any]) -> dict[str, t.Any]:
    truncated: t.Optional[dict[str, t.Any]] = None
    for key, value in meta.items():
        if isinstance(value, str) and len(value) > _MAX_META_TAG_VALUE_LENGTH:
            if truncated is None:
                truncated = dict(meta)
            truncated[key] = value[:_MAX_META_TAG_VALUE_LENGTH]

    return meta if truncated is None else truncated


def _truncate_payload_metadata(metadata: dict[str, dict[str, str]]) -> dict[str, dict[str, t.Any]]:
    truncated: t.Optional[dict[str, dict[str, t.Any]]] = None
    for event_type, event_metadata in metadata.items():
        truncated_event_metadata = _truncate_meta_string_values(event_metadata)
        if truncated_event_metadata is not event_metadata:
            if truncated is None:
                truncated = dict(metadata)
            truncated[event_type] = truncated_event_metadata

    return t.cast(dict[str, dict[str, t.Any]], metadata) if truncated is None else truncated


def _truncate_event_meta(event: Event) -> Event:
    content = event.get("content")
    if not isinstance(content, dict):
        return event

    meta = content.get("meta")
    if not isinstance(meta, dict):
        return event

    truncated_meta = _truncate_meta_string_values(meta)
    if truncated_meta is meta:
        return event

    return {
        **event,
        "content": {
            **content,
            "meta": truncated_meta,
        },
    }


def _truncate_events_meta(events: list[Event]) -> list[Event]:
    truncated_events: t.Optional[list[Event]] = None
    for index, event in enumerate(events):
        truncated_event = _truncate_event_meta(event)
        if truncated_event is not event:
            if truncated_events is None:
                truncated_events = list(events)
            truncated_events[index] = truncated_event

    return events if truncated_events is None else truncated_events


class BaseWriter(ABC):
    # After this many consecutive failed flushes (each already retried internally),
    # stop sending and drop events until the backend recovers.
    _MAX_CONSECUTIVE_FAILURES = 3

    def __init__(
        self,
        min_flush_events: t.Optional[int] = None,
        max_buffer_events: t.Optional[int] = None,
        async_flush_events: t.Optional[int] = None,
    ) -> None:
        self.lock = threading.RLock()
        self.should_finish = threading.Event()
        self._flush_now = threading.Event()
        self.flush_interval_seconds: float = 60
        self.min_flush_events = min_flush_events
        self.max_buffer_events = max_buffer_events
        self.async_flush_events = async_flush_events
        self.events: list[Event] = []
        self._consecutive_failures = 0
        self._dropped_events = 0
        # 4.5MB max uncompressed payload size, following <https://github.com/DataDog/datadog-ci-rb/pull/272>.
        self.max_payload_size = int(4.5 * 1024 * 1024)
        self._telemetry_api: t.Optional[TelemetryAPI] = None
        # Connectors registered here are closed from both the background thread
        # (via _task_teardown) and the caller thread (via wait_finish), covering
        # all thread-local HTTPConnections opened via BackendConnector.
        self._connectors: list[t.Any] = []

    @property
    def telemetry_api(self) -> TelemetryAPI:
        telemetry_api = self._telemetry_api
        if telemetry_api is None:
            telemetry_api = self._telemetry_api = TelemetryAPI.get()
        return telemetry_api

    def put_event(self, event: Event) -> None:
        with self.lock:
            if self.max_buffer_events is not None and len(self.events) >= self.max_buffer_events:
                self._dropped_events += 1
                if self._dropped_events % 1000 == 1:
                    log.warning(
                        "%s: buffer full (%d max), dropping events. %d dropped so far.",
                        self.__class__.__name__,
                        self.max_buffer_events,
                        self._dropped_events,
                    )
                return
            self.events.append(event)
            buffer_len = len(self.events)

        # Keep explicit partial-flush settings synchronous: users may configure them to reduce data loss
        # when a process crashes before normal shutdown. The default threshold flush is async to avoid
        # blocking the test runner on network I/O.
        if self.min_flush_events is not None and buffer_len >= self.min_flush_events:
            self.flush()
        elif self.async_flush_events is not None and buffer_len >= self.async_flush_events:
            self._flush_now.set()

    def pop_events(self) -> list[Event]:
        with self.lock:
            events = self.events
            self.events = []

        return events

    def start(self) -> None:
        self.task = threading.Thread(target=self._periodic_task, daemon=True)
        self.task.start()

    def set_async_flush_events(self, async_flush_events: t.Optional[int]) -> None:
        self.async_flush_events = async_flush_events

    def signal_finish(self) -> None:
        log.debug("Signalling for %s writer thread to finish", self.__class__.__name__)
        self.should_finish.set()
        self._flush_now.set()

    def wait_finish(self, timeout: t.Optional[float] = None) -> None:
        self.task.join(timeout=timeout)
        if self.task.is_alive():
            log.warning(
                "%s writer thread did not finish within %.1fs timeout; %d events may be lost.",
                self.__class__.__name__,
                timeout,
                len(self.events),
            )
        else:
            log.debug("%s writer thread finished", self.__class__.__name__)
        if self._dropped_events > 0:
            log.warning(
                "%s: %d events were dropped during this session.", self.__class__.__name__, self._dropped_events
            )
        # Close the caller thread's thread-local connection for each registered
        # connector (BackendConnector is threading.local, so this is safe even
        # when the caller thread never opened a connection).
        for connector in self._connectors:
            connector.close()

    def _task_teardown(self) -> None:
        # Close the background thread's thread-local connection for each
        # registered connector.
        for connector in self._connectors:
            connector.close()

    def _periodic_task(self) -> None:
        while True:
            self._flush_now.wait(timeout=self.flush_interval_seconds)
            self._flush_now.clear()
            log.debug("Flushing %s events in background task", self.__class__.__name__)
            try:
                self.flush()
            except Exception:
                log.exception("Unexpected error flushing %s events", self.__class__.__name__)

            # During shutdown, another thread can enqueue events while this thread is blocked in flush().
            # Do not exit until the buffer is empty; otherwise those events are stranded until process exit.
            if self.should_finish.is_set() and not self._has_events():
                break

        self._task_teardown()
        log.debug("Exiting %s background task", self.__class__.__name__)

    def _has_events(self) -> bool:
        with self.lock:
            return bool(self.events)

    def flush(self) -> None:
        events = self.pop_events()
        if not events:
            return

        # Circuit breaker: after repeated failures, drop events but probe
        # periodically (every _MAX_CONSECUTIVE_FAILURES flushes) to detect recovery.
        if self._consecutive_failures >= self._MAX_CONSECUTIVE_FAILURES:
            if (self._consecutive_failures + 1) % self._MAX_CONSECUTIVE_FAILURES != 0:
                log.debug(
                    "Dropping %d %s event(s): backend unreachable",
                    len(events),
                    self.__class__.__name__,
                )
                self._consecutive_failures += 1
                return
            log.debug("Probing backend after %d consecutive failures", self._consecutive_failures)

        log.debug("Sending %d events for %s", len(events), self.__class__.__name__)
        if self._send_events(events):
            if self._consecutive_failures > 0:
                log.info("%s: backend connectivity restored", self.__class__.__name__)
            self._consecutive_failures = 0
        else:
            self._consecutive_failures += 1
            if self._consecutive_failures == self._MAX_CONSECUTIVE_FAILURES:
                log.warning(
                    "%s: backend unreachable after %d consecutive failures, will drop events until recovery",
                    self.__class__.__name__,
                    self._consecutive_failures,
                )

    @abstractmethod
    def _send_events(self, events: list[Event]) -> bool:
        """Send events to the backend. Return True if all events were sent successfully."""
        pass

    @abstractmethod
    def _encode_events(self, events: list[Event]) -> bytes:
        pass

    def _split_pack_events(self, events: list[Event]) -> list[bytes]:
        pack = self._encode_events(events)

        if len(pack) > self.max_payload_size and len(events) > 1:
            del pack
            midpoint = len(events) // 2
            packs = self._split_pack_events(events[0:midpoint])
            packs += self._split_pack_events(events[midpoint:])
            return packs

        if len(pack) > self.max_payload_size:
            log.warning(
                "Single event payload (%d bytes) exceeds max size (%d bytes); sending anyway",
                len(pack),
                self.max_payload_size,
            )

        return [pack]


def _get_partial_flush_env() -> t.Optional[str]:
    return env.get("_DD_CIVISIBILITY_PARTIAL_FLUSH_MIN_SPANS", env.get("DD_TRACE_PARTIAL_FLUSH_MIN_SPANS"))


def _parse_positive_int_env(var_name: str, raw: t.Optional[str], description: str) -> t.Optional[int]:
    if raw is None:
        return None
    try:
        value = int(raw)
        return value if value > 0 else None
    except (ValueError, TypeError):
        log.warning("Invalid value for %s: %r; %s disabled", var_name, raw, description)
        return None


def _calculate_async_flush_events(selected_tests_count: t.Optional[int]) -> int:
    if selected_tests_count is None:
        return 100
    return max(100, min(1000, selected_tests_count // 10))


def _get_min_flush_events() -> t.Optional[int]:
    """Read the explicit synchronous partial-flush threshold, if configured."""
    raw = _get_partial_flush_env()
    if raw is None:
        return None
    return _parse_positive_int_env(
        "_DD_CIVISIBILITY_PARTIAL_FLUSH_MIN_SPANS / DD_TRACE_PARTIAL_FLUSH_MIN_SPANS",
        raw,
        "synchronous threshold-based flushing",
    )


def _get_async_flush_events(selected_tests_count: t.Optional[int] = None) -> t.Optional[int]:
    """Read the buffered event count that wakes the writer thread for an async flush.

    Without an explicit partial-flush setting, derive a linear threshold from selected
    tests bounded between 100 and 1000 events, defaulting to 100 before collection.
    Explicit partial-flush settings keep their existing synchronous behavior.
    """
    if _get_partial_flush_env() is not None:
        return None

    return _calculate_async_flush_events(selected_tests_count)


class TestOptWriter(BaseWriter):
    __test__ = False

    def __init__(self, connector_setup: BackendConnectorSetup) -> None:
        super().__init__(min_flush_events=_get_min_flush_events(), async_flush_events=_get_async_flush_events())

        self.metadata: dict[str, dict[str, str]] = {
            "*": {
                "language": "python",
                "runtime-id": uuid.uuid4().hex,
                "library_version": f"{__version__}",
                "_dd.origin": "ciapp-test",
                "_dd.p.dm": "-0",  # what is this?
            },
            "test": {
                # This should be framework specific, but we only support pytest for now.
                "_dd.library_capabilities.early_flake_detection": "1",
                "_dd.library_capabilities.auto_test_retries": "1",
                "_dd.library_capabilities.test_impact_analysis": "1",
                "_dd.library_capabilities.test_management.quarantine": "1",
                "_dd.library_capabilities.test_management.disable": "1",
                "_dd.library_capabilities.test_management.attempt_to_fix": "5",
                "_dd.library_capabilities.coverage_report_upload": "1",
            },
        }

        self.connector = connector_setup.get_connector_for_subdomain(Subdomain.CITESTCYCLE)
        self._connectors = [self.connector]

        self.serializers: dict[type[TestItem[t.Any, t.Any]], EventSerializer[t.Any]] = {
            TestRun: serialize_test_run,
            TestSuite: serialize_suite,
            TestModule: serialize_module,
            TestSession: serialize_session,
        }

    def add_metadata(self, event_type: str, metadata: dict[str, str]) -> None:
        self.metadata.setdefault(event_type, {}).update(metadata)

    def put_item(self, item: TestItem[t.Any, t.Any]) -> None:
        event = self.serializers[type(item)](item)
        self.put_event(event)

    def _test_cycle_payload(self, events: list[Event]) -> Event:
        return {
            "version": 1,
            "metadata": _truncate_payload_metadata(self.metadata),
            "events": _truncate_events_meta(events),
        }

    def _encode_events(self, events: list[Event]) -> bytes:
        return msgpack_packb(self._test_cycle_payload(events))

    def _send_events(self, events: list[Event]) -> bool:
        with StopWatch() as serialization_time:
            packs = self._split_pack_events(events)

        telemetry_api = self.telemetry_api
        telemetry_api.record_event_payload_serialization_seconds("test_cycle", serialization_time.elapsed())

        for pack in packs:
            result = self.connector.request(
                "POST",
                "/api/v2/citestcycle",
                data=pack,
                headers={"Content-Type": "application/msgpack"},
                send_gzip=True,
            )

            telemetry_api.record_event_payload(
                endpoint="test_cycle",
                payload_size=len(pack),
                request_seconds=result.elapsed_seconds,
                events_count=len(events),
                error=result.error_type,
            )

            if result.error_type:
                return False

        return True


class PayloadFileTestOptWriter(TestOptWriter):
    """TestOptWriter variant that writes JSON payload files instead of HTTP.

    Used in payload-files mode (Bazel). Filenames match Go's DDTestRunner
    naming pattern.
    """

    __test__ = False

    def __init__(self, connector_setup: BackendConnectorSetup, output_dir: str) -> None:
        super().__init__(connector_setup)
        self._output_dir = output_dir

    def _send_events(self, events: list[Event]) -> bool:
        write_payload_file(
            output_dir=self._output_dir,
            payload=self._test_cycle_payload(events),
            kind="tests",
        )
        return True


class TestCoverageWriter(BaseWriter):
    __test__ = False

    def __init__(self, connector_setup: BackendConnectorSetup) -> None:
        super().__init__(min_flush_events=_get_min_flush_events(), async_flush_events=_get_async_flush_events())

        self.connector = connector_setup.get_connector_for_subdomain(Subdomain.CITESTCOV)
        self._connectors = [self.connector]
        self._coverage_files_counts: list[int] = []
        self._coverage_empty_count = 0

    def _record_coverage_files_for_flush(self, files_count: int) -> None:
        with self.lock:
            self._coverage_files_counts.append(files_count)
            if files_count == 0:
                self._coverage_empty_count += 1

    def _flush_coverage_telemetry(self) -> None:
        with self.lock:
            coverage_files_counts = self._coverage_files_counts
            coverage_empty_count = self._coverage_empty_count
            self._coverage_files_counts = []
            self._coverage_empty_count = 0

        if not coverage_files_counts:
            return

        telemetry_api = self.telemetry_api
        for files_count in coverage_files_counts:
            telemetry_api.record_coverage_files(files_count)
        for _ in range(coverage_empty_count):
            telemetry_api.record_coverage_is_empty()

    def flush(self) -> None:
        self._flush_coverage_telemetry()
        super().flush()

    def put_coverage(self, test_run: TestRun, coverage_bitmaps: t.Iterable[tuple[str, bytes]]) -> None:
        files = [{"filename": pathname, "bitmap": bitmap} for pathname, bitmap in coverage_bitmaps]
        self._record_coverage_files_for_flush(len(files))

        if not files:
            return

        event = Event(
            test_session_id=test_run.session.item_id,
            test_suite_id=test_run.suite.item_id,
            span_id=test_run.span_id,
            files=files,
        )
        self.put_event(event)

    def put_suite_coverage(self, suite: TestSuite, coverage_bitmaps: t.Iterable[tuple[str, bytes]]) -> None:
        """Emit a coverage event keyed to the suite rather than an individual test run.

        Used in suite-skipping mode: the backend expects no span_id on coverage events so that
        it aggregates coverage at the suite level, matching the behaviour of the old plugin and
        the JS tracer.
        """
        files = [{"filename": pathname, "bitmap": bitmap} for pathname, bitmap in coverage_bitmaps]
        self._record_coverage_files_for_flush(len(files))

        if not files:
            return

        event = {
            "test_session_id": suite.session.item_id,
            "test_suite_id": suite.item_id,
            "files": files,
        }
        self.put_event(event)

    def _encode_events(self, events: list[Event]) -> bytes:
        return msgpack_packb({"version": 2, "coverages": events})

    def _send_events(self, events: list[Event]) -> bool:
        with StopWatch() as serialization_time:
            packs = self._split_pack_events(events)

        telemetry_api = self.telemetry_api
        telemetry_api.record_event_payload_serialization_seconds("code_coverage", serialization_time.elapsed())

        for pack in packs:
            files = [
                FileAttachment(
                    name="coverage1",
                    filename="coverage1.msgpack",
                    content_type="application/msgpack",
                    data=pack,
                ),
                FileAttachment(
                    name="event",
                    filename="event.json",
                    content_type="application/json",
                    data=b'{"dummy":true}',
                ),
            ]

            result = self.connector.post_files("/api/v2/citestcov", files=files, send_gzip=True)

            telemetry_api.record_event_payload(
                endpoint="code_coverage",
                payload_size=len(pack),
                request_seconds=result.elapsed_seconds,
                events_count=len(events),
                error=result.error_type,
            )

            if result.error_type:
                return False

        return True


class PayloadFileCoverageWriter(TestCoverageWriter):
    """TestCoverageWriter variant that writes JSON payload files instead of HTTP.

    Used in payload-files mode (Bazel). Coverage bitmaps (bytes) are
    base64-encoded so they survive JSON serialization.
    """

    __test__ = False

    def __init__(self, connector_setup: BackendConnectorSetup, output_dir: str) -> None:
        super().__init__(connector_setup)
        self._output_dir = output_dir

    def _send_events(self, events: list[Event]) -> bool:
        encoded_events = []
        for event in events:
            event_copy = dict(event)
            if "files" in event_copy:
                event_copy["files"] = [
                    {
                        **f,
                        "bitmap": base64.b64encode(f["bitmap"]).decode("ascii")
                        if isinstance(f.get("bitmap"), bytes)
                        else f.get("bitmap"),
                    }
                    for f in event_copy["files"]
                ]
            encoded_events.append(event_copy)

        write_payload_file(
            output_dir=self._output_dir,
            payload={"version": 2, "coverages": encoded_events},
            kind="coverage",
        )
        return True


def serialize_test_run(test_run: TestRun) -> Event:
    return Event(
        version=2,
        type="test",
        content={
            "trace_id": test_run.trace_id,
            "parent_id": 1,
            "span_id": test_run.span_id,
            "service": test_run.service,
            "resource": test_run.name,
            "name": "pytest.test",
            "error": 1 if test_run.get_status() == TestStatus.FAIL else 0,
            "start": test_run.start_ns,
            "duration": test_run.duration_ns,
            "meta": {
                "span.kind": "test",
                "test.module": test_run.module.name,
                "test.module_path": test_run.module.module_path,
                "test.name": test_run.name,
                "test.status": test_run.get_status().value,
                "test.suite": test_run.suite.name,
                "type": "test",
                **test_run.test.tags,
                **test_run.tags,
            },
            "metrics": {
                "_dd.py.partial_flush": 1,
                "_dd.top_level": 1,
                "_dd.tracer_kr": 1.0,
                "_sampling_priority_v1": 1,
                **test_run.metrics,
            },
            "type": "test",
            "test_session_id": test_run.session.item_id,
            "test_module_id": test_run.module.item_id,
            "test_suite_id": test_run.suite.item_id,
        },
    )


def serialize_suite(suite: TestSuite) -> Event:
    return Event(
        version=1,
        type="test_suite_end",
        content={
            "service": suite.service,
            "resource": suite.name,
            "name": "pytest.test_suite",
            "error": 0,
            "start": suite.start_ns,
            "duration": suite.duration_ns,
            "meta": {
                "span.kind": "test",
                "test.suite": suite.name,
                "test.status": suite.get_status().value,
                "type": "test_suite_end",
                **suite.tags,
            },
            "metrics": {
                "_dd.py.partial_flush": 1,
                "_dd.tracer_kr": 1.0,
                "_sampling_priority_v1": 1,
                **suite.metrics,
            },
            "type": "test_suite_end",
            "test_session_id": suite.session.item_id,
            "test_module_id": suite.module.item_id,
            "test_suite_id": suite.item_id,
        },
    )


def serialize_module(module: TestModule) -> Event:
    return Event(
        version=1,
        type="test_module_end",
        content={
            "service": module.service,
            "resource": module.name,
            "name": "pytest.test_module",
            "error": 0,
            "start": module.start_ns,
            "duration": module.duration_ns,
            "meta": {
                "span.kind": "test",
                "test.module": module.name,
                "test.module_path": module.module_path,
                "test.status": module.get_status().value,
                "type": "test_module_end",
                **module.tags,
            },
            "metrics": {
                "_dd.py.partial_flush": 1,
                "_dd.tracer_kr": 1.0,
                "_sampling_priority_v1": 1,
                **module.metrics,
            },
            "type": "test_module_end",
            "test_session_id": module.session.item_id,
            "test_module_id": module.item_id,
        },
    )


def serialize_session(session: TestSession) -> Event:
    return Event(
        version=1,
        type="test_session_end",
        content={
            "service": session.service,
            "resource": session.name,
            "name": "pytest.test_session",
            "error": 0,
            "start": session.start_ns,
            "duration": session.duration_ns,
            "meta": {
                "span.kind": "test",
                "test.status": session.get_status().value,
                "type": "test_session_end",
                **session.tags,
            },
            "metrics": {
                "_dd.py.partial_flush": 1,
                "_dd.top_level": 1,
                "_dd.tracer_kr": 1.0,
                "_sampling_priority_v1": 1,
                **session.metrics,
            },
            "type": "test_session_end",
            "test_session_id": session.item_id,
        },
    )
