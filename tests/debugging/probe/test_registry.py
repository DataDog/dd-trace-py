from ddtrace.debugging._probe.model import LineProbe
from ddtrace.debugging._probe.registry import ProbeRegistry
from tests.debugging.probe.test_status import DummyProbeStatusLogger


def test_registry_contains():
    probe_in = LineProbe(probe_id=42, source_file="foo", line=1)
    probe_out = LineProbe(probe_id=0, source_file="foo", line=2)

    registry = ProbeRegistry(DummyProbeStatusLogger("test", "test"))
    registry.register(probe_in)

    assert probe_in in registry
    assert probe_out not in registry


def test_registry_pending():
    # Start with registering 10 probes
    probes = [LineProbe(probe_id=i, source_file=__file__, line=i) for i in range(10)]

    registry = ProbeRegistry(DummyProbeStatusLogger("test", "test"))
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


def test_registry_location_error():
    status_logger = DummyProbeStatusLogger("test", "test")
    registry = ProbeRegistry(status_logger)

    probe = LineProbe(probe_id=42, source_file=__file__, line=1)

    # Ensure the probe has no location information
    probe.source_file = None

    registry.register(probe)

    # Check that the probe is not pending
    assert not registry.get_pending(__file__)

    # Check that we emitted the correct diagnostic error message
    assert status_logger.queue == [
        {
            "service": "test",
            "message": "Unable to resolve location information for probe 42",
            "ddsource": "dd_debugger",
            "debugger": {"diagnostics": {"probeId": 42, "status": "ERROR"}},
        }
    ]
