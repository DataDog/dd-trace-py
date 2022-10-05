from time import sleep

import pytest

from ddtrace.debugging._config import config
from ddtrace.debugging._probe.model import LineProbe
from ddtrace.debugging._probe.model import Probe
from ddtrace.debugging._probe.remoteconfig import ProbePollerEvent
from ddtrace.debugging._probe.remoteconfig import ProbeRCAdapter
from ddtrace.debugging._probe.remoteconfig import _filter_by_env_and_version
from ddtrace.internal.compat import PY2
from tests.utils import override_global_config


class MockConfig(object):
    def __init__(self, *args, **kwargs):
        self.probes = {}

    @_filter_by_env_and_version
    def get_probes(self, _):
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


@pytest.mark.parametrize(
    "env,version,expected",
    [
        (None, None, set(["probe4"])),
        (None, "dev", set(["probe3", "probe4"])),
        ("prod", None, set(["probe2", "probe4"])),
        ("prod", "dev", set(["probe1", "probe2", "probe3", "probe4"])),
    ],
)
def test_poller_env_version(env, version, expected, mock_config):
    probes = []

    def cb(e, ps):
        probes.extend(ps)

    with override_global_config(dict(env=env, version=version)):
        mock_config.add_probes(
            [
                LineProbe(
                    probe_id="probe1",
                    source_file="tests/debugger/submod/stuff.py",
                    line=36,
                    condition=None,
                    tags={"env": "prod", "version": "dev"},
                ),
                LineProbe(
                    probe_id="probe2",
                    source_file="tests/debugger/submod/stuff.py",
                    line=36,
                    condition=None,
                    tags={"env": "prod"},
                ),
                LineProbe(
                    probe_id="probe3",
                    source_file="tests/debugger/submod/stuff.py",
                    line=36,
                    condition=None,
                    tags={"version": "dev"},
                ),
                LineProbe(
                    probe_id="probe4",
                    source_file="tests/debugger/submod/stuff.py",
                    line=36,
                    condition=None,
                ),
            ]
        )

        ProbeRCAdapter(cb)(None, {})

        assert set(_.probe_id for _ in probes) == expected


@pytest.mark.xfail(PY2, reason="occasionally fails on Python 2")
def test_poller_events(mock_config):
    events = set()

    def cb(e, ps):
        events.add((e, frozenset([p.probe_id if isinstance(p, Probe) else p for p in ps])))

    mock_config.add_probes(
        [
            LineProbe(
                probe_id="probe1",
                source_file="tests/debugger/submod/stuff.py",
                line=36,
                condition=None,
            ),
            LineProbe(
                probe_id="probe2",
                source_file="tests/debugger/submod/stuff.py",
                line=36,
                condition=None,
            ),
            LineProbe(
                probe_id="probe3",
                source_file="tests/debugger/submod/stuff.py",
                line=36,
                condition=None,
            ),
            LineProbe(
                probe_id="probe4",
                source_file="tests/debugger/submod/stuff.py",
                line=36,
                condition=None,
            ),
        ]
    )

    old_interval = config.diagnostics_interval
    config.diagnostics_interval = 0.5
    try:
        adapter = ProbeRCAdapter(cb)

        adapter(None, {})
        mock_config.remove_probes("probe1", "probe2")
        mock_config.add_probes(
            [
                # Modified
                LineProbe(
                    probe_id="probe2",
                    source_file="tests/debugger/submod/stuff.py",
                    line=36,
                    condition=None,
                    active=False,
                ),
                # New
                LineProbe(
                    probe_id="probe5",
                    source_file="tests/debugger/submod/stuff.py",
                    line=36,
                    condition=None,
                    active=False,
                ),
            ]
        )
        adapter(None, {})

        # Wait to allow the next call to the adapter to generate a status event
        sleep(0.5)
        adapter(None, {})

        assert events == {
            (ProbePollerEvent.NEW_PROBES, frozenset(["probe4", "probe1", "probe2", "probe3"])),
            (ProbePollerEvent.DELETED_PROBES, frozenset(["probe1"])),
            (ProbePollerEvent.MODIFIED_PROBES, frozenset(["probe2"])),
            (ProbePollerEvent.NEW_PROBES, frozenset(["probe5"])),
            (ProbePollerEvent.STATUS_UPDATE, frozenset(["probe4", "probe2", "probe3", "probe5"])),
        }
    finally:
        config.diagnostics_interval = old_interval
