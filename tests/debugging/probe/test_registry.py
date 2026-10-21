from ddtrace.debugging._probe.registry import ProbeRegistry
from ddtrace.internal import runtime
from tests.debugging.probe.test_status import DummyProbeStatusLogger
from tests.debugging.utils import create_snapshot_line_probe


def test_registry_contains():
    probe_in = create_snapshot_line_probe(probe_id=42, source_file="foo", line=1)
    probe_out = create_snapshot_line_probe(probe_id=0, source_file="foo", line=2)

    registry = ProbeRegistry(DummyProbeStatusLogger("test"))
    registry.register(probe_in)

    assert probe_in in registry
    assert probe_out not in registry


def test_registry_pending():
    # Start with registering 10 probes
    probes = [create_snapshot_line_probe(probe_id=i, source_file=__file__, line=i) for i in range(10)]

    registry = ProbeRegistry(DummyProbeStatusLogger("test"))
    registry.register(*probes)

    assert registry.get_pending(__file__) == probes

    installed_probes, pending_probes = probes[:5], probes[5:]

    # The first 5 probes are installed
    for probe in installed_probes:
        registry.set_installed(probe)

    assert registry.get_pending(__file__) == pending_probes
    assert all(p in registry for p in probes)

    unreg_probes, pending_probes = pending_probes[:3], pending_probes[3:]

    # Some pending probes are now unregistered
    registry.unregister(*unreg_probes)

    assert registry.get_pending(__file__) == pending_probes
    assert all(p not in registry for p in unreg_probes)


def test_registry_reset_pending():
    probes = [create_snapshot_line_probe(probe_id=i, source_file=__file__, line=i) for i in range(4)]

    registry = ProbeRegistry(DummyProbeStatusLogger("test"))
    registry.register(*probes)

    installed_probes, pending_probes = probes[:3], probes[3:]
    for probe in installed_probes:
        registry.set_installed(probe)
    # Installing a probe more than once must not track it more than once
    registry.set_installed(installed_probes[0])
    registry.set_emitting(installed_probes[0])

    # An unregistered installed probe must not come back as pending
    registry.unregister(installed_probes[2])

    registry.reset_pending(__file__)

    assert registry.get_pending(__file__) == pending_probes + installed_probes[:2]
    assert not any(registry[p.probe_id].installed for p in installed_probes[:2])
    # A reset probe must not report as emitting until it does so again
    assert not registry[installed_probes[0].probe_id].emitting

    # Nothing is installed any longer, so a further reset is a no-op
    registry.reset_pending(__file__)
    assert registry.get_pending(__file__) == pending_probes + installed_probes[:2]


def test_registry_location_error():
    status_logger = DummyProbeStatusLogger("test")
    registry = ProbeRegistry(status_logger)

    probe = create_snapshot_line_probe(probe_id=42, source_file=__file__, line=1)

    # Ensure the probe has no location information
    probe.resolved_source_file = None

    registry.register(probe)

    # Check that the probe is not pending
    assert not registry.get_pending(__file__)

    # Check that unregistering the probe does not cause an exception
    registry.unregister(probe)

    # Check that we emitted the correct diagnostic error message
    for e in status_logger.queue:
        del e["timestamp"]
    assert status_logger.queue == [
        {
            "service": "test",
            "message": "Failed to instrument probe 42",
            "ddsource": "dd_debugger",
            "type": "diagnostic",
            "debugger": {
                "diagnostics": {
                    "probeId": 42,
                    "probeVersion": 0,
                    "runtimeId": runtime.get_runtime_id(),
                    "parentId": None,
                    "status": "ERROR",
                    "exception": {
                        "type": "UnresolvedLocation",
                        "message": "Unable to resolve location information for probe 42",
                    },
                }
            },
        }
    ]
