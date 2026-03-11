import random
from unittest import mock
from uuid import uuid4

import pytest

from ddtrace.debugging._config import di_config
from ddtrace.debugging._probe.model import DEFAULT_PROBE_RATE
from ddtrace.debugging._probe.model import DEFAULT_SNAPSHOT_PROBE_RATE
from ddtrace.debugging._probe.model import LogProbeMixin
from ddtrace.debugging._probe.model import Probe
from ddtrace.debugging._probe.model import ProbeType
from ddtrace.debugging._probe.remoteconfig import DebuggerRCCallback
from ddtrace.debugging._probe.remoteconfig import InvalidProbeConfiguration
from ddtrace.debugging._probe.remoteconfig import ProbePollerEvent
from ddtrace.debugging._probe.remoteconfig import _filter_by_env_and_version
from ddtrace.debugging._probe.remoteconfig import build_probe
from ddtrace.debugging._probe.status import ProbeStatusLogger
from ddtrace.internal.remoteconfig import Payload
from ddtrace.internal.remoteconfig.client import ConfigMetadata
from tests.debugging.utils import create_snapshot_line_probe
from tests.utils import override_global_config


class MockConfig(object):
    def __init__(self, *args, **kwargs):
        self.probes = {}

    @_filter_by_env_and_version
    def get_probes(self, _conf, status_logger):
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


class _MockSubscriber:
    """Mock subscriber for test compatibility with the new single-subscriber architecture."""

    def __init__(self, debugger_callback):
        self._debugger_callback = debugger_callback
        self.periodic = lambda: None  # No-op, tests call methods manually

    def _send_status_update(self):
        """Trigger a status update event."""
        self._debugger_callback._callback(ProbePollerEvent.STATUS_UPDATE, [])


class ProbeRCAdapter:
    """Test helper that simulates remote config updates for debugging probes.

    This replaces the old PubSub-based adapter with a simpler interface that
    directly uses DebuggerRCCallback from the new single-subscriber architecture.
    """

    def __init__(self, _preprocess_results, callback, status_logger, registry=None, diagnostics_interval=60.0):
        # Create a mock registry if none provided
        if registry is None:
            from unittest.mock import Mock

            registry = Mock()
            registry.log_probes_status = Mock()

        self._debugger_callback = DebuggerRCCallback(callback, status_logger, registry, diagnostics_interval)
        self._pending_payloads = []
        self._preprocess_results = _preprocess_results
        # Mock subscriber for test compatibility
        self._subscriber = _MockSubscriber(self._debugger_callback)

    def __call__(self, payloads):
        """Make the adapter callable for registration with remoteconfig_poller."""
        return self._debugger_callback(payloads)

    def append(self, config_content, target, config_metadata):
        """Add a config payload to the pending list."""
        self._pending_payloads.append(Payload(config_metadata, target, config_content))

    def publish(self):
        """Process all pending payloads through the debugger callback."""
        if self._pending_payloads:
            payloads = self._pending_payloads
            self._pending_payloads = []
            self._debugger_callback(payloads)

    def append_and_publish(self, config_content, target, config_metadata):
        """Add a config payload and immediately process it."""
        self.append(config_content, target, config_metadata)
        self.publish()


class SyncProbeRCAdapter(ProbeRCAdapter):
    """Synchronous test adapter - same as ProbeRCAdapter since we removed background threads."""

    def __init__(self, *args, **kwargs):
        status_logger = kwargs.pop("status_logger", ProbeStatusLogger("test"))
        super(SyncProbeRCAdapter, self).__init__(*args, **kwargs, status_logger=status_logger)


def config_metadata(config_id=None):
    if config_id is None:
        config_id = uuid4()
    return ConfigMetadata(config_id, product_name="LIVE_DEBUGGING", sha256_hash="hash", length=123, tuf_version=1)


@pytest.fixture
def mock_config():
    import ddtrace.debugging._probe.remoteconfig as rc

    original_get_probes = rc.get_probes
    mock_config = MockConfig()
    try:
        rc.get_probes = mock_config.get_probes

        yield mock_config
    finally:
        rc.get_probes = original_get_probes


@pytest.fixture
def mock_config_exc():
    import ddtrace.debugging._probe.remoteconfig as rc

    original_build_probe = rc.build_probe

    def build_probe(config):
        raise Exception("test exception")

    try:
        rc.build_probe = build_probe

        yield
    finally:
        rc.build_probe = original_build_probe


@pytest.mark.parametrize(
    "env,version,expected",
    [
        (None, None, set(["probe4"])),
        (None, "dev", set(["probe3", "probe4"])),
        ("prod", None, set(["probe2", "probe4"])),
        ("prod", "dev", set(["probe1", "probe2", "probe3", "probe4"])),
    ],
)
def test_poller_env_version(env, version, expected, rc_poller, mock_config):
    probes = []

    def callback(e, ps, *args, **kwargs):
        probes.extend(ps)

    with override_global_config(dict(env=env, version=version)):
        mock_config.add_probes(
            [
                create_snapshot_line_probe(
                    probe_id="probe1",
                    source_file="tests/debugger/submod/stuff.py",
                    line=36,
                    condition=None,
                    tags={"env": "prod", "version": "dev"},
                ),
                create_snapshot_line_probe(
                    probe_id="probe2",
                    source_file="tests/debugger/submod/stuff.py",
                    line=36,
                    condition=None,
                    tags={"env": "prod"},
                ),
                create_snapshot_line_probe(
                    probe_id="probe3",
                    source_file="tests/debugger/submod/stuff.py",
                    line=36,
                    condition=None,
                    tags={"version": "dev"},
                ),
                create_snapshot_line_probe(
                    probe_id="probe4",
                    source_file="tests/debugger/submod/stuff.py",
                    line=36,
                    condition=None,
                    tags={},
                ),
            ]
        )

        adapter = SyncProbeRCAdapter(None, callback)
        rc_poller.register_callback("TEST", adapter)
        adapter.append_and_publish({"test": random.randint(0, 11111111)}, "", config_metadata())
        rc_poller.poll()

        assert set(_.probe_id for _ in probes) == expected


def test_poller_remove_probe(rc_poller):
    events = set()

    def cb(e, ps):
        events.add((e, frozenset({p.probe_id if isinstance(p, Probe) else p for p in ps})))

    old_interval = di_config.diagnostics_interval
    di_config.diagnostics_interval = 0.5
    try:
        adapter = SyncProbeRCAdapter(None, cb)

        rc_poller.register_callback("TEST", adapter)
        adapter.append_and_publish(
            {
                "id": "probe1",
                "version": 0,
                "type": ProbeType.SPAN_PROBE,
                "active": True,
                "tags": ["foo:bar"],
                "where": {"type": "Stuff", "method": "foo"},
                "resource": "resourceX",
            },
            "",
            config_metadata("probe1"),
        )
        rc_poller.poll()

        assert events == {
            (ProbePollerEvent.NEW_PROBES, frozenset({"probe1"})),
        }

        adapter.append_and_publish(
            None,
            "",
            config_metadata("probe1"),
        )
        rc_poller.poll()

        assert events == {
            (ProbePollerEvent.NEW_PROBES, frozenset({"probe1"})),
            (ProbePollerEvent.DELETED_PROBES, frozenset({"probe1"})),
        }

    finally:
        di_config.diagnostics_interval = old_interval


def test_poller_remove_multiple_probe(rc_poller):
    events = set()

    def cb(e, ps):
        events.add((e, frozenset({p.probe_id if isinstance(p, Probe) else p for p in ps})))

    old_interval = di_config.diagnostics_interval
    di_config.diagnostics_interval = float("inf")
    try:
        adapter = SyncProbeRCAdapter(None, cb)
        rc_poller.register_callback("TEST", adapter)
        adapter.append(
            {
                "id": "probe1",
                "version": 0,
                "type": ProbeType.SPAN_PROBE,
                "active": True,
                "tags": ["foo:bar"],
                "where": {"type": "Stuff", "method": "foo"},
                "resource": "resourceX",
            },
            "",
            config_metadata("probe1"),
        )
        adapter.append(
            {
                "id": "probe2",
                "version": 0,
                "type": ProbeType.SPAN_PROBE,
                "active": True,
                "tags": ["foo:bar"],
                "where": {"type": "Stuff", "method": "foo"},
                "resource": "resourceX",
            },
            "",
            config_metadata("probe2"),
        )
        adapter.publish()
        rc_poller.poll()

        assert events == {
            (ProbePollerEvent.NEW_PROBES, frozenset({"probe2"})),
            (ProbePollerEvent.NEW_PROBES, frozenset({"probe1"})),
        }

        adapter.append_and_publish(
            False,
            "",
            config_metadata("probe1"),
        )
        rc_poller.poll()

        assert events == {
            (ProbePollerEvent.NEW_PROBES, frozenset({"probe2"})),
            (ProbePollerEvent.NEW_PROBES, frozenset({"probe1"})),
            (ProbePollerEvent.DELETED_PROBES, frozenset({"probe1"})),
        }

        adapter.append_and_publish(
            None,
            "",
            config_metadata("probe2"),
        )
        rc_poller.poll()

        assert events == {
            (ProbePollerEvent.NEW_PROBES, frozenset({"probe2"})),
            (ProbePollerEvent.NEW_PROBES, frozenset({"probe1"})),
            (ProbePollerEvent.DELETED_PROBES, frozenset({"probe1"})),
            (ProbePollerEvent.DELETED_PROBES, frozenset({"probe2"})),
        }
    finally:
        di_config.diagnostics_interval = old_interval


def test_poller_events(rc_poller, mock_config):
    events = set()

    def callback(e, ps, *args, **kwargs):
        events.add((e, frozenset([p.probe_id if isinstance(p, Probe) else p for p in ps])))

    mock_config.add_probes(
        [
            create_snapshot_line_probe(
                probe_id="probe1",
                source_file="tests/debugger/submod/stuff.py",
                line=36,
                condition=None,
            ),
            create_snapshot_line_probe(
                probe_id="probe2",
                source_file="tests/debugger/submod/stuff.py",
                line=36,
                condition=None,
            ),
            create_snapshot_line_probe(
                probe_id="probe3",
                source_file="tests/debugger/submod/stuff.py",
                line=36,
                condition=None,
            ),
            create_snapshot_line_probe(
                probe_id="probe4",
                source_file="tests/debugger/submod/stuff.py",
                line=36,
                condition=None,
            ),
        ]
    )

    metadata = config_metadata()
    old_interval = di_config.diagnostics_interval
    di_config.diagnostics_interval = float("inf")
    try:
        adapter = SyncProbeRCAdapter(None, callback)
        rc_poller.register_callback("TEST2", adapter)
        adapter.append_and_publish({"test": 2}, "", metadata)
        rc_poller.poll()
        mock_config.remove_probes("probe1", "probe2")
        mock_config.add_probes(
            [
                # Modified
                create_snapshot_line_probe(
                    probe_id="probe2",
                    source_file="tests/debugger/submod/stuff.py",
                    line=36,
                    condition=None,
                ),
                # New
                create_snapshot_line_probe(
                    probe_id="probe5",
                    source_file="tests/debugger/submod/stuff.py",
                    line=36,
                    condition=None,
                ),
            ]
        )
        metadata.sha256_hash = "hash3"
        adapter.append_and_publish({"test": 3}, "", metadata)
        rc_poller.poll()

        adapter._subscriber._send_status_update()

        metadata.sha256_hash = "hash4"
        adapter.append_and_publish({"test": 4}, "", metadata)
        rc_poller.poll()

        adapter._subscriber._send_status_update()

        assert events == {
            (ProbePollerEvent.NEW_PROBES, frozenset(["probe4", "probe1", "probe2", "probe3"])),
            (ProbePollerEvent.DELETED_PROBES, frozenset(["probe1"])),
            (ProbePollerEvent.NEW_PROBES, frozenset(["probe5"])),
            (ProbePollerEvent.STATUS_UPDATE, frozenset()),
        }, events
    finally:
        di_config.diagnostics_interval = old_interval


def test_multiple_configs(rc_poller):
    events = set()

    def cb(e, ps):
        events.add((e, frozenset({p.probe_id if isinstance(p, Probe) else p for p in ps})))

    old_interval = di_config.diagnostics_interval
    di_config.diagnostics_interval = float("inf")
    try:
        adapter = SyncProbeRCAdapter(None, cb)
        # Wait to allow the next call to the adapter to generate a status event
        rc_poller.register_callback("TEST", adapter)
        adapter.append_and_publish(
            {
                "id": "probe1",
                "version": 0,
                "type": ProbeType.SPAN_PROBE,
                "active": True,
                "tags": ["foo:bar"],
                "where": {"type": "Stuff", "method": "foo"},
                "resource": "resourceX",
            },
            "",
            config_metadata("spanProbe_probe1"),
        )
        rc_poller.poll()

        assert events == {
            (ProbePollerEvent.NEW_PROBES, frozenset({"probe1"})),
        }

        adapter.append_and_publish(
            {
                "id": "probe2",
                "version": 1,
                "type": ProbeType.METRIC_PROBE,
                "tags": ["foo:bar"],
                "where": {"sourceFile": "tests/submod/stuff.p", "lines": ["36"]},
                "metricName": "test.counter",
                "kind": "COUNTER",
            },
            "",
            config_metadata("metricProbe_probe2"),
        )
        rc_poller.poll()

        assert events == {
            (ProbePollerEvent.NEW_PROBES, frozenset({"probe1"})),
            (ProbePollerEvent.NEW_PROBES, frozenset({"probe2"})),
        }

        adapter.append_and_publish(
            {
                "id": "probe3",
                "version": 1,
                "type": ProbeType.LOG_PROBE,
                "tags": ["foo:bar"],
                "where": {"sourceFile": "tests/submod/stuff.p", "lines": ["36"]},
                "template": "hello {#foo}",
                "segments:": [{"str": "hello "}, {"dsl": "foo", "json": "#foo"}],
            },
            "",
            config_metadata("logProbe_probe3"),
        )
        rc_poller.poll()

        assert events == {
            (ProbePollerEvent.NEW_PROBES, frozenset({"probe1"})),
            (ProbePollerEvent.NEW_PROBES, frozenset({"probe2"})),
            (ProbePollerEvent.NEW_PROBES, frozenset({"probe3"})),
        }

        adapter._subscriber._send_status_update()

        with pytest.raises(InvalidProbeConfiguration):
            adapter.append_and_publish({}, "", config_metadata("not-supported"))
            rc_poller.poll()

        assert events == {
            (ProbePollerEvent.NEW_PROBES, frozenset({"probe1"})),
            (ProbePollerEvent.NEW_PROBES, frozenset({"probe2"})),
            (ProbePollerEvent.NEW_PROBES, frozenset({"probe3"})),
            (ProbePollerEvent.STATUS_UPDATE, frozenset()),
        }

        # remove configuration
        adapter.append_and_publish(None, "", config_metadata("metricProbe_probe2"))
        rc_poller.poll()

        assert events == {
            (ProbePollerEvent.NEW_PROBES, frozenset({"probe1"})),
            (ProbePollerEvent.NEW_PROBES, frozenset({"probe2"})),
            (ProbePollerEvent.NEW_PROBES, frozenset({"probe3"})),
            (ProbePollerEvent.STATUS_UPDATE, frozenset()),
            (ProbePollerEvent.DELETED_PROBES, frozenset({"probe2"})),
        }

    finally:
        di_config.diagnostics_interval = old_interval


def test_log_probe_attributes_parsing():
    probe = build_probe(
        {
            "id": "3d338829-21c4-4a8a-8a1a-71fbce995efa",
            "version": 0,
            "type": ProbeType.LOG_PROBE,
            "language": "python",
            "where": {
                "sourceFile": "foo.py",
                "lines": ["57"],
            },
            "tags": ["env:staging", "version:v12417452-d2552757"],
            "template": "{weekID} {idea}",
            "segments": [
                {"dsl": "weekID", "json": {"eq": [{"ref": "weekID"}, 1]}},
                {"str": " "},
                {"dsl": "idea", "json": {"ref": "idea"}},
            ],
            "captureSnapshot": False,
            "capture": {"maxReferenceDepth": 42, "maxLength": 43},
            "sampling": {"snapshotsPerSecond": 5000},
        }
    )

    assert isinstance(probe, LogProbeMixin)

    assert probe.limits.max_level == 42
    assert probe.limits.max_len == 43


def test_parse_log_probe_with_rate():
    probe = build_probe(
        {
            "id": "3d338829-21c4-4a8a-8a1a-71fbce995efa",
            "version": 0,
            "type": ProbeType.LOG_PROBE,
            "tags": ["foo:bar"],
            "where": {"sourceFile": "tests/submod/stuff.p", "lines": ["36"]},
            "template": "hello {#foo}",
            "segments:": [{"str": "hello "}, {"dsl": "foo", "json": "#foo"}],
            "sampling": {"snapshotsPerSecond": 1337},
        }
    )

    assert probe.rate == 1337


def test_parse_log_probe_default_rates():
    probe = build_probe(
        {
            "id": "3d338829-21c4-4a8a-8a1a-71fbce995efa",
            "version": 0,
            "type": ProbeType.LOG_PROBE,
            "tags": ["foo:bar"],
            "where": {"sourceFile": "tests/submod/stuff.p", "lines": ["36"]},
            "template": "hello {#foo}",
            "segments:": [{"str": "hello "}, {"dsl": "foo", "json": "#foo"}],
            "captureSnapshot": True,
        }
    )

    assert probe.rate == DEFAULT_SNAPSHOT_PROBE_RATE

    probe = build_probe(
        {
            "id": "3d338829-21c4-4a8a-8a1a-71fbce995efa",
            "version": 0,
            "type": ProbeType.LOG_PROBE,
            "tags": ["foo:bar"],
            "where": {"sourceFile": "tests/submod/stuff.p", "lines": ["36"]},
            "template": "hello {#foo}",
            "segments:": [{"str": "hello "}, {"dsl": "foo", "json": "#foo"}],
            "captureSnapshot": False,
        }
    )

    assert probe.rate == DEFAULT_PROBE_RATE


def test_parse_metric_probe_with_probeid_tags():
    probeId = "3d338829-21c4-4a8a-8a1a-71fbce995efa"
    probe = build_probe(
        {
            "id": probeId,
            "version": 0,
            "type": ProbeType.METRIC_PROBE,
            "tags": ["foo:bar"],
            "where": {"sourceFile": "tests/submod/stuff.p", "lines": ["36"]},
            "metricName": "test.counter",
            "kind": "COUNTER",
        }
    )

    assert probe.tags["debugger.probeid"] == probeId


def test_modified_probe_events(rc_poller, mock_config):
    events = []

    def cb(e, ps):
        events.append((e, frozenset([p.probe_id if isinstance(p, Probe) else p for p in ps])))

    mock_config.add_probes(
        [
            create_snapshot_line_probe(
                probe_id="probe1",
                version=1,
                source_file="tests/debugger/submod/stuff.py",
                line=36,
                condition=None,
            ),
        ]
    )

    metadata = config_metadata()
    old_interval = di_config.diagnostics_interval
    di_config.diagnostics_interval = float("inf")
    try:
        adapter = SyncProbeRCAdapter(None, cb)
        # Wait to allow the next call to the adapter to generate a status event
        rc_poller.register_callback("TEST", adapter)

        adapter._subscriber._send_status_update()

        adapter.append_and_publish({"test": 5}, "", metadata)
        rc_poller.poll()

        mock_config.add_probes(
            [
                create_snapshot_line_probe(
                    probe_id="probe1",
                    version=2,
                    source_file="tests/debugger/submod/stuff.py",
                    line=36,
                    condition=None,
                )
            ]
        )
        metadata.sha256_hash = "hash6"
        adapter.append_and_publish({"test": 6}, "", metadata)
        rc_poller.poll()

        adapter._subscriber._send_status_update()

        metadata.sha256_hash = "hash7"
        adapter.append_and_publish({"test": 7}, "", metadata)
        rc_poller.poll()

        assert events == [
            (ProbePollerEvent.STATUS_UPDATE, frozenset()),
            (ProbePollerEvent.NEW_PROBES, frozenset(["probe1"])),
            (ProbePollerEvent.MODIFIED_PROBES, frozenset(["probe1"])),
            (ProbePollerEvent.STATUS_UPDATE, frozenset()),
        ]
    finally:
        di_config.diagnostics_interval = old_interval


def test_expression_compilation_error(rc_poller, mock_config_exc):
    events = []

    def cb(e, ps):
        events.append((e, frozenset([p.probe_id if isinstance(p, Probe) else p for p in ps])))

    metadata = config_metadata()
    old_interval = di_config.diagnostics_interval
    di_config.diagnostics_interval = float("inf")
    try:
        status_logger = mock.Mock()
        adapter = SyncProbeRCAdapter(None, cb, status_logger=status_logger)
        # Wait to allow the next call to the adapter to generate a status event
        rc_poller.register_callback("TEST", adapter)

        adapter.append_and_publish({"id": "error", "version": 0}, "", metadata)
        rc_poller.poll()

        status_logger.error.assert_called_once()
        assert status_logger.error.call_args[1]["error"] == ("Exception", "test exception")
        assert status_logger.error.call_args[1]["probe"].probe_id == "error"
    finally:
        di_config.diagnostics_interval = old_interval
