from abc import ABC
from abc import abstractmethod
import logging
import threading
import typing as t
import uuid

from ddtrace.testing.internal.http import BackendConnectorSetup
from ddtrace.testing.internal.http import FileAttachment
from ddtrace.testing.internal.http import Subdomain
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


class BaseWriter(ABC):
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.should_finish = threading.Event()
        self.flush_interval_seconds = 60
        self.events: t.List[Event] = []
        # 4.5MB max uncompressed payload size, following <https://github.com/DataDog/datadog-ci-rb/pull/272>.
        self.max_payload_size = int(4.5 * 1024 * 1024)

    def put_event(self, event: Event) -> None:
        with self.lock:
            self.events.append(event)

    def pop_events(self) -> t.List[Event]:
        with self.lock:
            events = self.events
            self.events = []

        return events

    def start(self) -> None:
        self.task = threading.Thread(target=self._periodic_task, daemon=True)
        self.task.start()

    def signal_finish(self) -> None:
        log.debug("Signalling for %s writer thread to finish", self.__class__.__name__)
        self.should_finish.set()

    def wait_finish(self) -> None:
        self.task.join()
        log.debug("%s writer thread finished", self.__class__.__name__)

    def _periodic_task(self) -> None:
        while True:
            self.should_finish.wait(timeout=self.flush_interval_seconds)
            log.debug("Flushing %s events in background task", self.__class__.__name__)
            self.flush()

            if self.should_finish.is_set():
                break

        log.debug("Exiting %s background task", self.__class__.__name__)

    def flush(self) -> None:
        if events := self.pop_events():
            log.debug("Sending %d events for %s", len(events), self.__class__.__name__)
            self._send_events(events)

    @abstractmethod
    def _send_events(self, events: t.List[Event]) -> None:
        pass

    @abstractmethod
    def _encode_events(self, events: t.List[Event]) -> bytes:
        pass

    def _split_pack_events(self, events: t.List[Event]) -> t.List[bytes]:
        pack = self._encode_events(events)

        if len(pack) > self.max_payload_size and len(events) > 1:
            del pack
            midpoint = len(events) // 2
            packs = self._split_pack_events(events[0:midpoint])
            packs += self._split_pack_events(events[midpoint:])
            return packs
        else:
            return [pack]


class TestOptWriter(BaseWriter):
    __test__ = False

    def __init__(self, connector_setup: BackendConnectorSetup) -> None:
        super().__init__()

        self.metadata: t.Dict[str, t.Dict[str, str]] = {
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
            },
        }

        self.connector = connector_setup.get_connector_for_subdomain(Subdomain.CITESTCYCLE)

        self.serializers: t.Dict[t.Type[TestItem[t.Any, t.Any]], EventSerializer[t.Any]] = {
            TestRun: serialize_test_run,
            TestSuite: serialize_suite,
            TestModule: serialize_module,
            TestSession: serialize_session,
        }

    def add_metadata(self, event_type: str, metadata: t.Dict[str, str]) -> None:
        self.metadata[event_type].update(metadata)

    def put_item(self, item: TestItem[t.Any, t.Any]) -> None:
        event = self.serializers[type(item)](item)
        self.put_event(event)

    def _encode_events(self, events: t.List[Event]) -> bytes:
        payload = {
            "version": 1,
            "metadata": self.metadata,
            "events": events,
        }
        return msgpack_packb(payload)

    def _send_events(self, events: t.List[Event]) -> None:
        with StopWatch() as serialization_time:
            packs = self._split_pack_events(events)

        TelemetryAPI.get().record_event_payload_serialization_seconds("test_cycle", serialization_time.elapsed())

        for pack in packs:
            result = self.connector.request(
                "POST",
                "/api/v2/citestcycle",
                data=pack,
                headers={"Content-Type": "application/msgpack"},
                send_gzip=True,
            )

            TelemetryAPI.get().record_event_payload(
                endpoint="test_cycle",
                payload_size=len(pack),
                request_seconds=result.elapsed_seconds,
                events_count=len(events),
                error=result.error_type,
            )


class TestCoverageWriter(BaseWriter):
    __test__ = False

    def __init__(self, connector_setup: BackendConnectorSetup) -> None:
        super().__init__()

        self.connector = connector_setup.get_connector_for_subdomain(Subdomain.CITESTCOV)

    def put_coverage(self, test_run: TestRun, coverage_bitmaps: t.Iterable[t.Tuple[str, bytes]]) -> None:
        files = [{"filename": pathname, "bitmap": bitmap} for pathname, bitmap in coverage_bitmaps]
        TelemetryAPI.get().record_coverage_files(len(files))

        if not files:
            TelemetryAPI.get().record_coverage_is_empty()
            return

        event = Event(
            test_session_id=test_run.session.item_id,
            test_suite_id=test_run.suite.item_id,
            span_id=test_run.span_id,
            files=files,
        )
        self.put_event(event)

    def _encode_events(self, events: t.List[Event]) -> bytes:
        return msgpack_packb({"version": 2, "coverages": events})

    def _send_events(self, events: t.List[Event]) -> None:
        with StopWatch() as serialization_time:
            packs = self._split_pack_events(events)

        TelemetryAPI.get().record_event_payload_serialization_seconds("code_coverage", serialization_time.elapsed())

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

            TelemetryAPI.get().record_event_payload(
                endpoint="code_coverage",
                payload_size=len(pack),
                request_seconds=result.elapsed_seconds,
                events_count=len(events),
                error=result.error_type,
            )


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
