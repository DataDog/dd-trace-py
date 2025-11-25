from abc import ABC
from abc import abstractmethod
import logging
import threading
import typing as t
import uuid

from ddtrace.internal._encoding import packb as msgpack_packb
from ddtrace.testing.internal.http import BackendConnectorSetup
from ddtrace.testing.internal.http import FileAttachment
from ddtrace.testing.internal.test_data import TestItem
from ddtrace.testing.internal.test_data import TestModule
from ddtrace.testing.internal.test_data import TestRun
from ddtrace.testing.internal.test_data import TestSession
from ddtrace.testing.internal.test_data import TestStatus
from ddtrace.testing.internal.test_data import TestSuite
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

    def put_event(self, event: Event) -> None:
        # TODO: compute/estimate payload size as events are inserted, and force a push once we reach a certain size.
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

    def finish(self) -> None:
        log.debug("Waiting for writer thread to finish")
        self.should_finish.set()
        self.task.join()
        log.debug("Writer thread finished")

    def _periodic_task(self) -> None:
        while True:
            self.should_finish.wait(timeout=self.flush_interval_seconds)
            log.debug("Flushing events in background task")
            self.flush()

            if self.should_finish.is_set():
                break

        log.debug("Exiting background task")

    def flush(self) -> None:
        if events := self.pop_events():
            log.debug("Sending %d events", len(events))
            self._send_events(events)

    @abstractmethod
    def _send_events(self, events: t.List[Event]) -> None:
        pass


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
                "_dd.library_capabilities.test_management.attempt_to_fix": "4",
            },
        }

        self.connector = connector_setup.get_connector_for_subdomain("citestcycle-intake")

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

    def _send_events(self, events: t.List[Event]) -> None:
        payload = {
            "version": 1,
            "metadata": self.metadata,
            "events": events,
        }
        pack = msgpack_packb(payload)
        response, response_data = self.connector.request(
            "POST", "/api/v2/citestcycle", data=pack, headers={"Content-Type": "application/msgpack"}, send_gzip=True
        )


class TestCoverageWriter(BaseWriter):
    __test__ = False

    def __init__(self, connector_setup: BackendConnectorSetup) -> None:
        super().__init__()

        self.connector = connector_setup.get_connector_for_subdomain("citestcov-intake")

    def put_coverage(self, test_run: TestRun, coverage_bitmaps: t.Iterable[t.Tuple[str, bytes]]) -> None:
        files = [{"filename": pathname, "bitmap": bitmap} for pathname, bitmap in coverage_bitmaps]
        if not files:
            return

        event = Event(
            test_session_id=test_run.session.item_id,
            test_suite_id=test_run.suite.item_id,
            span_id=test_run.span_id,
            files=files,
        )
        self.put_event(event)

    def _send_events(self, events: t.List[Event]) -> None:
        files = [
            FileAttachment(
                name="coverage1",
                filename="coverage1.msgpack",
                content_type="application/msgpack",
                data=msgpack_packb({"version": 2, "coverages": events}),
            ),
            FileAttachment(
                name="event",
                filename="event.json",
                content_type="application/json",
                data=b'{"dummy":true}',
            ),
        ]

        response, response_data = self.connector.post_files("/api/v2/citestcov", files=files, send_gzip=True)


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
                **test_run.test.tags,
                **test_run.tags,
                "span.kind": "test",
                "test.module": test_run.module.name,
                "test.module_path": test_run.module.module_path,
                "test.name": test_run.name,
                "test.status": test_run.get_status().value,
                "test.suite": test_run.suite.name,
                "test.type": "test",
                "type": "test",
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
                **suite.tags,
                "span.kind": "test",
                "test.suite": suite.name,
                "test.status": suite.get_status().value,
                "type": "test_suite_end",
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
                **module.tags,
                "span.kind": "test",
                "test.module": module.name,
                "test.module_path": module.module_path,
                "test.status": module.get_status().value,
                "type": "test_module_end",
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
                **session.tags,
                "span.kind": "test",
                "test.status": session.get_status().value,
                "type": "test_session_end",
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
