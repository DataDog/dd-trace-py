from ddtrace.debugging._debugger import Debugger
from ddtrace.debugging._probe.remoteconfig import ProbePollerEvent


class BMDebugger(Debugger):
    pending_probes = []

    @classmethod
    def enable(cls):
        super(BMDebugger, cls).enable()

        cls.add_probes(*cls.pending_probes)

        cls.pending_probes = []

    @classmethod
    def add_probes(cls, *probes):
        if cls._instance is None:
            cls.pending_probes.extend(probes)
        else:
            cls._instance._on_configuration(ProbePollerEvent.NEW_PROBES, probes)
