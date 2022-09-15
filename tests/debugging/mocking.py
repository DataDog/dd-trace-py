import atexit
from contextlib import contextmanager
import json
from typing import Any

from ddtrace.debugging._config import config
from ddtrace.debugging._debugger import Debugger
from ddtrace.debugging._probe.model import Probe
from ddtrace.debugging._probe.remoteconfig import ProbePollerEvent
from ddtrace.debugging._probe.remoteconfig import _filter_by_env_and_version
from ddtrace.debugging._snapshot.collector import SnapshotCollector
from ddtrace.debugging._uploader import LogsIntakeUploaderV1
from tests.debugging.probe.test_status import DummyProbeStatusLogger


class MockLogsIntakeUploaderV1(LogsIntakeUploaderV1):
    def __init__(self, encoder, interval=0.1):
        super(MockLogsIntakeUploaderV1, self).__init__(encoder, interval)
        self.queue = []

    def _write(self, payload):
        self.queue.append(payload.decode())

    @property
    def payloads(self):
        return [json.loads(data) for data in self.queue]


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
    def __init__(self, service, encoder):
        super(MockProbeStatusLogger, self).__init__(service, encoder)
        self.queue = []


class TestSnapshotCollector(SnapshotCollector):
    def __init__(self, *args, **kwargs):
        # type: (*Any, **Any) -> None
        super(TestSnapshotCollector, self).__init__(*args, **kwargs)
        self.test_queue = []

    def _enqueue(self, snapshot):
        self.test_queue.append(snapshot)
        return super(TestSnapshotCollector, self)._enqueue(snapshot)


class TestDebugger(Debugger):
    __logger__ = MockProbeStatusLogger
    __uploader__ = MockLogsIntakeUploaderV1
    __collector__ = TestSnapshotCollector

    def add_probes(self, *probes):
        # type: (Probe) -> None
        self._on_configuration(ProbePollerEvent.NEW_PROBES, probes)

    def remove_probes(self, *probes):
        # type: (Probe) -> None
        self._on_configuration(ProbePollerEvent.DELETED_PROBES, probes)

    @property
    def test_queue(self):
        return self._collector.test_queue

    @property
    def uploader(self):
        return self._uploader

    @property
    def probe_status_logger(self):
        return self._probe_registry.logger


@contextmanager
def debugger(**config_overrides):
    # type: (Any) -> None
    """Test with the debugger enabled."""
    atexit_register = atexit.register
    try:
        old_config = config.__dict__
        config.__dict__ = dict(old_config)
        config.__dict__.update(config_overrides)

        atexit.register = lambda _: None

        TestDebugger.enable()
        assert TestDebugger._instance is not None

        yield TestDebugger._instance

    finally:
        try:
            TestDebugger.disable()
            assert TestDebugger._instance is None
            config.__dict__ = old_config
        finally:
            atexit.register = atexit_register
