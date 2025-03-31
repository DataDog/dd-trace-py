import random
from uuid import uuid4

import mock
import pytest

from ddtrace.debugging._config import di_config
from ddtrace.debugging._probe.model import DEFAULT_PROBE_RATE
from ddtrace.debugging._probe.model import DEFAULT_SNAPSHOT_PROBE_RATE
from ddtrace.debugging._probe.model import LogProbeMixin
from ddtrace.debugging._probe.model import Probe
from ddtrace.debugging._probe.model import ProbeType
from ddtrace.debugging._probe.remoteconfig import InvalidProbeConfiguration
from ddtrace.debugging._probe.remoteconfig import ProbePollerEvent
from ddtrace.debugging._probe.remoteconfig import ProbeRCAdapter
from ddtrace.debugging._probe.remoteconfig import _filter_by_env_and_version
from ddtrace.debugging._probe.remoteconfig import build_probe
from ddtrace.debugging._probe.status import ProbeStatusLogger
from ddtrace.internal.remoteconfig.client import ConfigMetadata
from ddtrace.internal.remoteconfig.worker import remoteconfig_poller
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


class SyncProbeRCAdapter(ProbeRCAdapter):
    def __init__(self, *args, **kwargs):
        status_logger = kwargs.pop("status_logger", ProbeStatusLogger("test"))
        super(SyncProbeRCAdapter, self).__init__(*args, **kwargs, status_logger=status_logger)
        # Make the subscriber worker thread a no-op. We call methods manually.
        self._subscriber.periodic = self.periodic

    def periodic(self):
        pass


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

        yield mock_config
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
def test_poller_env_version(env, version, expected, remote_config_worker, mock_config):
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
                ),
            ]
        )

        adapter = SyncProbeRCAdapter(None, callback)
        remoteconfig_poller.register("TEST", adapter, skip_enabled=True)
        adapter.append_and_publish({"test": random.randint(0, 11111111)}, "", config_metadata())
        remoteconfig_poller._poll_data()

        assert set(_.probe_id for _ in probes) == expected


def test_poller_remove_probe():
    events = set()

    def cb(e, ps):
        events.add((e, frozenset({p.probe_id if isinstance(p, Probe) else p for p in ps})))

    old_interval = di_config.diagnostics_interval
    di_config.diagnostics_interval = 0.5
    try:
        adapter = SyncProbeRCAdapter(None, cb)

        remoteconfig_poller.register("TEST", adapter, skip_enabled=True)
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
        remoteconfig_poller._poll_data()

        assert events == {
            (ProbePollerEvent.NEW_PROBES, frozenset({"probe1"})),
        }

        adapter.append_and_publish(
            None,
            "",
            config_metadata("probe1"),
        )
        remoteconfig_poller._poll_data()

        assert events == {
            (ProbePollerEvent.NEW_PROBES, frozenset({"probe1"})),
            (ProbePollerEvent.DELETED_PROBES, frozenset({"probe1"})),
        }

    finally:
        di_config.diagnostics_interval = old_interval


def test_poller_remove_multiple_probe():
    events = set()

    def cb(e, ps):
        events.add((e, frozenset({p.probe_id if isinstance(p, Probe) else p for p in ps})))

    old_interval = di_config.diagnostics_interval
    di_config.diagnostics_interval = float("inf")
    try:
        adapter = SyncProbeRCAdapter(None, cb)
        remoteconfig_poller.register("TEST", adapter, skip_enabled=True)
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
        remoteconfig_poller._poll_data()

        assert events == {
            (ProbePollerEvent.NEW_PROBES, frozenset({"probe2"})),
            (ProbePollerEvent.NEW_PROBES, frozenset({"probe1"})),
        }

        adapter.append_and_publish(
            False,
            "",
            config_metadata("probe1"),
        )
        remoteconfig_poller._poll_data()

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
        remoteconfig_poller._poll_data()

        assert events == {
            (ProbePollerEvent.NEW_PROBES, frozenset({"probe2"})),
            (ProbePollerEvent.NEW_PROBES, frozenset({"probe1"})),
            (ProbePollerEvent.DELETED_PROBES, frozenset({"probe1"})),
            (ProbePollerEvent.DELETED_PROBES, frozenset({"probe2"})),
        }
    finally:
        di_config.diagnostics_interval = old_interval


def test_poller_events(remote_config_worker, mock_config):
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
        remoteconfig_poller.register("TEST2", adapter, skip_enabled=True)
        adapter.append_and_publish({"test": 2}, "", metadata)
        remoteconfig_poller._poll_data()
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
        remoteconfig_poller._poll_data()

        adapter._subscriber._send_status_update()

        metadata.sha256_hash = "hash4"
        adapter.append_and_publish({"test": 4}, "", metadata)
        remoteconfig_poller._poll_data()

        adapter._subscriber._send_status_update()

        assert events == {
            (ProbePollerEvent.NEW_PROBES, frozenset(["probe4", "probe1", "probe2", "probe3"])),
            (ProbePollerEvent.DELETED_PROBES, frozenset(["probe1"])),
            (ProbePollerEvent.NEW_PROBES, frozenset(["probe5"])),
            (ProbePollerEvent.STATUS_UPDATE, frozenset()),
        }, events
    finally:
        di_config.diagnostics_interval = old_interval


def test_multiple_configs(remote_config_worker):
    events = set()

    def cb(e, ps):
        events.add((e, frozenset({p.probe_id if isinstance(p, Probe) else p for p in ps})))

    old_interval = di_config.diagnostics_interval
    di_config.diagnostics_interval = float("inf")
    try:
        adapter = SyncProbeRCAdapter(None, cb)
        # Wait to allow the next call to the adapter to generate a status event
        remoteconfig_poller.register("TEST", adapter, skip_enabled=True)
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
        remoteconfig_poller._poll_data()

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
        remoteconfig_poller._poll_data()

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
        remoteconfig_poller._poll_data()

        assert events == {
            (ProbePollerEvent.NEW_PROBES, frozenset({"probe1"})),
            (ProbePollerEvent.NEW_PROBES, frozenset({"probe2"})),
            (ProbePollerEvent.NEW_PROBES, frozenset({"probe3"})),
        }

        adapter._subscriber._send_status_update()

        with pytest.raises(InvalidProbeConfiguration):
            adapter.append_and_publish({}, "", config_metadata("not-supported"))
            remoteconfig_poller._poll_data()

        assert events == {
            (ProbePollerEvent.NEW_PROBES, frozenset({"probe1"})),
            (ProbePollerEvent.NEW_PROBES, frozenset({"probe2"})),
            (ProbePollerEvent.NEW_PROBES, frozenset({"probe3"})),
            (ProbePollerEvent.STATUS_UPDATE, frozenset()),
        }

        # remove configuration
        adapter.append_and_publish(None, "", config_metadata("metricProbe_probe2"))
        remoteconfig_poller._poll_data()

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


def test_modified_probe_events(remote_config_worker, mock_config):
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
        remoteconfig_poller.register("TEST", adapter, skip_enabled=True)

        adapter._subscriber._send_status_update()

        adapter.append_and_publish({"test": 5}, "", metadata)
        remoteconfig_poller._poll_data()

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
        remoteconfig_poller._poll_data()

        adapter._subscriber._send_status_update()

        metadata.sha256_hash = "hash7"
        adapter.append_and_publish({"test": 7}, "", metadata)
        remoteconfig_poller._poll_data()

        assert events == [
            (ProbePollerEvent.STATUS_UPDATE, frozenset()),
            (ProbePollerEvent.NEW_PROBES, frozenset(["probe1"])),
            (ProbePollerEvent.MODIFIED_PROBES, frozenset(["probe1"])),
            (ProbePollerEvent.STATUS_UPDATE, frozenset()),
        ]
    finally:
        di_config.diagnostics_interval = old_interval


def test_expression_compilation_error(remote_config_worker, mock_config_exc):
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
        remoteconfig_poller.register("TEST", adapter, skip_enabled=True)

        adapter.append_and_publish({"id": "error", "version": 0}, "", metadata)
        remoteconfig_poller._poll_data()

        status_logger.error.assert_called_once()
        assert status_logger.error.call_args[1]["error"] == ("Exception", "test exception")
        assert status_logger.error.call_args[1]["probe"].probe_id == "error"
    finally:
        di_config.diagnostics_interval = old_interval
