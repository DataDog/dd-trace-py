from time import sleep

import pytest

from ddtrace.debugging._probe.model import LineProbe
from ddtrace.debugging._probe.poller import ProbePoller
from ddtrace.debugging._probe.poller import ProbePollerEvent
from ddtrace.debugging._remoteconfig import _filter_by_env_and_version
from tests.utils import override_global_config


class MockDebuggerRC(object):
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


@pytest.mark.parametrize(
    "env,version,expected",
    [
        (None, None, set(["probe4"])),
        (None, "dev", set(["probe3", "probe4"])),
        ("prod", None, set(["probe2", "probe4"])),
        ("prod", "dev", set(["probe1", "probe2", "probe3", "probe4"])),
    ],
)
def test_poller_env_version(env, version, expected):
    probes = []

    def cb(e, ps):
        probes.extend(ps)

    with override_global_config(dict(env=env, version=version)):
        api = MockDebuggerRC()
        api.add_probes(
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
        poller = ProbePoller(api, cb, interval=0.1)
        poller.start()
        sleep(0.2)
        poller.stop()
        assert set(_.probe_id for _ in probes) == expected


def test_poller_events():
    events = set()
    status_update_events = set()

    def cb(e, ps):
        if e is ProbePollerEvent.STATUS_UPDATE:
            status_update_events.add(ps)
        else:
            events.add((e, frozenset([p.probe_id for p in ps])))

    api = MockDebuggerRC()
    api.add_probes(
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
    poller = ProbePoller(api, cb, interval=0.1)
    poller.start()
    sleep(0.2)
    api.remove_probes("probe1", "probe2")
    api.add_probes(
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
    sleep(0.2)
    poller.stop()

    assert events == {
        (ProbePollerEvent.NEW_PROBES, frozenset(["probe4", "probe1", "probe2", "probe3"])),
        (ProbePollerEvent.DELETED_PROBES, frozenset(["probe1"])),
        (ProbePollerEvent.MODIFIED_PROBES, frozenset(["probe2"])),
        (ProbePollerEvent.NEW_PROBES, frozenset(["probe5"])),
    }
