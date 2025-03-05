import atexit
from collections import Counter
from contextlib import contextmanager
import json
from time import monotonic
from time import sleep
from typing import Any
from typing import Generator
from typing import List

from ddtrace.debugging._config import di_config
from ddtrace.debugging._debugger import Debugger
from ddtrace.debugging._exception.replay import SpanExceptionHandler
from ddtrace.debugging._probe.model import Probe
from ddtrace.debugging._probe.remoteconfig import ProbePollerEvent
from ddtrace.debugging._probe.remoteconfig import _filter_by_env_and_version
from ddtrace.debugging._signal.collector import SignalCollector
from ddtrace.debugging._signal.snapshot import Snapshot
from ddtrace.debugging._uploader import LogsIntakeUploaderV1
from ddtrace.settings._core import DDConfig
from tests.debugging.probe.test_status import DummyProbeStatusLogger


class PayloadWaitTimeout(Exception):
    pass


class MockDebuggingRCV07(object):
    def __init__(self, *args, **kwargs):
        self.probes = {}

    @_filter_by_env_and_version
    def get_probes(self):
        return list(self.probes.values())

    def add_probes(self, probes):
        for probe in probes:
            self.probes[probe.probe_id] = probe

    def remove_probes(self, *probe_ids):
        for probe_id in probe_ids:
            try:
                del self.probes[probe_id]
            except KeyError:
                pass


class MockProbeStatusLogger(DummyProbeStatusLogger):
    def wait(self, cond, timeout=1.0):
        end = monotonic() + timeout

        while monotonic() < end:
            if cond(self.queue):
                return True
            sleep(0.01)

        raise PayloadWaitTimeout()


class TestSignalCollector(SignalCollector):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super(TestSignalCollector, self).__init__(*args, **kwargs)
        self.test_queue = []
        self.signal_state_counter = Counter()

    def push(self, signal):
        self.signal_state_counter.update({signal.state: 1})
        super(TestSignalCollector, self).push(signal)

    def _enqueue(self, snapshot):
        self.test_queue.append(snapshot)
        return super(TestSignalCollector, self)._enqueue(snapshot)

    @property
    def queue(self):
        return self.test_queue

    def wait(self, cond=lambda q: q, timeout=1.0):
        end = monotonic() + timeout

        while monotonic() <= end:
            if cond(self.test_queue):
                return self.test_queue
            sleep(0.01)

        raise PayloadWaitTimeout()


class MockLogsIntakeUploaderV1(LogsIntakeUploaderV1):
    __collector__ = TestSignalCollector

    def __init__(self, interval=0.0):
        super(MockLogsIntakeUploaderV1, self).__init__(interval)
        self.queue = []

    def _write(self, payload):
        self.queue.append(payload.decode())

    def wait_for_payloads(self, cond=lambda _: bool(_), timeout=1.0):
        _cond = (lambda _: len(_) == cond) if isinstance(cond, int) else cond

        end = monotonic() + timeout

        while not _cond(self.payloads):
            if monotonic() > end:
                raise PayloadWaitTimeout(_cond, timeout)
            sleep(0.05)

        return self.payloads

    @property
    def collector(self):
        return self._collector

    @property
    def payloads(self):
        return [_ for data in self.queue for _ in json.loads(data)]

    @property
    def snapshots(self) -> List[Snapshot]:
        return self.collector.queue


class TestDebugger(Debugger):
    __logger__ = MockProbeStatusLogger
    __uploader__ = MockLogsIntakeUploaderV1

    def add_probes(self, *probes: Probe) -> None:
        self._on_configuration(ProbePollerEvent.NEW_PROBES, probes)

    def remove_probes(self, *probes: Probe) -> None:
        self._on_configuration(ProbePollerEvent.DELETED_PROBES, probes)

    def modify_probes(self, *probes: Probe) -> None:
        self._on_configuration(ProbePollerEvent.MODIFIED_PROBES, probes)

    @property
    def test_queue(self):
        return self.collector.test_queue

    @property
    def signal_state_counter(self):
        return self.collector.signal_state_counter

    @property
    def uploader(self):
        return self.__uploader__._instance

    @property
    def collector(self):
        return self.__uploader__.get_collector()

    @property
    def snapshots(self) -> List[Snapshot]:
        return self.uploader.snapshots

    @property
    def probe_status_logger(self):
        return self._probe_registry.logger

    def log_probe_status(self):
        self._probe_registry.log_probes_status()

    def assert_no_snapshots(self):
        assert len(self.test_queue) == 0

    @contextmanager
    def assert_single_snapshot(self):
        self.uploader.wait_for_payloads()

        assert len(self.test_queue) == 1

        yield self.test_queue[0]


@contextmanager
def _debugger(config_to_override: DDConfig, config_overrides: Any) -> Generator[TestDebugger, None, None]:
    """Test with the debugger enabled."""
    atexit_register = atexit.register
    try:
        old_config = config_to_override.__dict__
        config_to_override.__dict__ = dict(old_config)
        config_to_override.__dict__.update(config_overrides)

        atexit.register = lambda _: None

        TestDebugger.enable()
        assert TestDebugger._instance is not None

        yield TestDebugger._instance

    finally:
        try:
            TestDebugger.disable()
            assert TestDebugger._instance is None
            config_to_override.__dict__ = old_config
        finally:
            atexit.register = atexit_register


@contextmanager
def debugger(**config_overrides: Any) -> Generator[TestDebugger, None, None]:
    """Test with the debugger enabled."""
    with _debugger(di_config, config_overrides) as debugger:
        debugger.__watchdog__.install()
        try:
            yield debugger
        finally:
            debugger.__watchdog__.uninstall()


class MockSpanExceptionHandler(SpanExceptionHandler):
    __uploader__ = MockLogsIntakeUploaderV1


@contextmanager
def exception_replay(**config_overrides: Any) -> Generator[MockLogsIntakeUploaderV1, None, None]:
    MockSpanExceptionHandler.enable()

    handler = MockSpanExceptionHandler._instance
    try:
        yield handler.__uploader__._instance
    finally:
        MockSpanExceptionHandler.disable()
