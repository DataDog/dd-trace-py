import os
import sys
from threading import Thread
from types import FrameType
from types import ModuleType
import typing as t

from _config import config

from ddtrace.context import Context
from ddtrace.debugging._config import config as debugger_config
from ddtrace.debugging._debugger import Debugger
from ddtrace.debugging._debugger import DebuggerModuleWatchdog
from ddtrace.debugging._encoding import SnapshotJsonEncoder
from ddtrace.debugging._function.discovery import FunctionDiscovery
from ddtrace.debugging._probe.model import ConditionalProbe
from ddtrace.debugging._probe.model import Probe
from ddtrace.debugging._probe.remoteconfig import ProbePollerEvent
from ddtrace.debugging._snapshot.collector import SnapshotCollector
from ddtrace.debugging._snapshot.collector import SnapshotContext
from ddtrace.debugging._snapshot.model import Snapshot
from ddtrace.internal.compat import ExcInfoType
from ddtrace.internal.compat import PY3
from ddtrace.internal.module import origin


try:
    COLS, _ = os.get_terminal_size()
except Exception:
    COLS = 80
CWD = os.path.abspath(os.getcwd())
TESTS = os.path.join(CWD, "test")


def from_editable_install(module):
    # type: (ModuleType) -> bool
    o = origin(module)
    return o.startswith(CWD) and not o.startswith(TESTS) and (config.venv is None or not o.startswith(config.venv))


def is_included(module):
    # type: (ModuleType) -> bool
    segments = module.__name__.split(".")
    for i in config.include:
        if i == segments[: len(i)]:
            return True
    return False


def is_ddtrace(module):
    # type: (ModuleType) -> bool
    name = module.__name__
    return name == "ddtrace" or name.startswith("ddtrace.")


class ModuleCollector(DebuggerModuleWatchdog):
    def __init__(self, *args, **kwargs):
        super(ModuleCollector, self).__init__(*args, **kwargs)

        self._imported_modules = set()  # type: t.Set[str]

    def on_collect(self, discovery):
        # type: (FunctionDiscovery) -> None
        raise NotImplementedError()

    def _on_new_module(self, module):
        try:
            if not is_ddtrace(module):
                if config.include:
                    if not is_included(module):
                        return
                elif not from_editable_install(module):
                    # We want to instrument only the modules that belong to the
                    # codebase and exclude the modules that belong to the tests
                    # and the dependencies installed within the virtual env.
                    return

                try:
                    return self.on_collect(FunctionDiscovery(module))
                except Exception as e:
                    status("Error collecting functions from %s: %s" % (module.__name__, e))
                    raise e

            status("Excluding module %s" % module.__name__)

        except Exception as e:
            status("Error after module import %s: %s" % (module.__name__, e))
            raise e

    def after_import(self, module):
        # type: (ModuleType) -> None
        name = module.__name__
        if name in self._imported_modules:
            return

        self._imported_modules.add(name)

        self._on_new_module(module)

        super(ModuleCollector, self).after_import(module)

        if PY3 and config.elusive:
            # Handle any new modules that have been imported since the last time
            # and that have eluded the import hook.
            for m in list(_ for _ in sys.modules.values() if _ is not None):
                # In Python 3 we can check if a module has been fully
                # initialised. At this stage we want to skip anything that is
                # only partially initialised.
                try:
                    if m.__spec__._initializing:
                        continue
                except AttributeError:
                    continue

                name = m.__name__
                if name not in self._imported_modules:
                    self._imported_modules.add(name)
                    self._on_new_module(m)
                    super(ModuleCollector, self).after_import(m)


class NoopDebuggerRC(object):
    def __init__(self, *args, **kwargs):
        pass


class NoopService(object):
    def __init__(self, *args, **kwargs):
        pass

    def stop(self):
        pass

    def start(self):
        pass

    def join(self):
        pass


class NoopProbePoller(NoopService):
    pass


class NoopLogsIntakeUploader(NoopService):
    pass


class NoopProbeStatusLogger(object):
    def __init__(self, *args, **kwargs):
        pass

    def received(self, *args, **kwargs):
        pass

    def installed(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


class NoopSnapshotJsonEncoder(SnapshotJsonEncoder):
    def encode(self, snapshot):
        # type: (Snapshot) -> bytes
        return b""

    @classmethod
    def capture_context(
        cls,
        arguments,  # type: t.List[t.Tuple[str, t.Any]]
        _locals,  # type: t.List[t.Tuple[str, t.Any]]
        throwable,  # type: ExcInfoType
        level=1,  # type: int
    ):
        # type: (...) -> t.Dict[str, t.Any]
        return {}


class ExplorationSnapshotCollector(SnapshotCollector):
    def __init__(self, *args, **kwargs):
        super(ExplorationSnapshotCollector, self).__init__(*args, **kwargs)
        encoder_class = SnapshotJsonEncoder if config.encode else NoopSnapshotJsonEncoder
        self._encoder = encoder_class("exploration")
        self._encoder._encoders = {Snapshot: self._encoder}
        self._snapshots = []
        self._probes = []
        self._failed_encoding = []
        self.on_snapshot = None

    def _enqueue(self, snapshot):
        # type: (Snapshot) -> None
        if config.encode:
            try:
                self._snapshots.append(self._encoder.encode(snapshot))
            except Exception:
                self._failed_encoding.append(snapshot)

        self._probes.append(snapshot.probe)
        if self.on_snapshot:
            self.on_snapshot(snapshot)

    def collect(
        self,
        probe,  # type: ConditionalProbe
        frame,  # type: FrameType
        thread,  # type: Thread
        args,  # type: t.List[t.Tuple[str, t.Any]]
        context=None,  # type: t.Optional[Context]
    ):
        # type: (...) -> SnapshotContext
        return SnapshotContext(self, probe, frame, thread, args, context)

    @property
    def snapshots(self):
        # type: () -> t.List[Snapshot]
        return self._snapshots or [None]

    @property
    def probes(self):
        # type: () -> t.List[Probe]
        return self._probes or [None]


class ExplorationDebugger(Debugger):
    __rc__ = NoopDebuggerRC
    __uploader__ = NoopLogsIntakeUploader
    __collector__ = ExplorationSnapshotCollector
    __watchdog__ = ModuleCollector
    __logger__ = NoopProbeStatusLogger
    __poller__ = NoopProbePoller

    @classmethod
    def on_disable(cls):
        # type: () -> None
        raise NotImplementedError()

    @classmethod
    def on_snapshot(cls, snapshot):
        # type: (Snapshot) -> None
        pass

    @classmethod
    def enable(cls):
        # type: () -> None
        debugger_config.max_probes = float("inf")
        debugger_config.global_rate_limit = float("inf")
        debugger_config.metrics = False

        super(ExplorationDebugger, cls).enable()

        cls._instance._collector.on_snapshot = cls.on_snapshot

    @classmethod
    def disable(cls):
        # type: () -> None
        registry = cls._instance._probe_registry

        nprobes = len(registry)
        nokprobes = sum(_.installed for _ in registry.values())

        print(("{:=^%ds}" % COLS).format(" %s: probes stats " % cls.__name__))
        print("")

        print("Installed probes: %d/%d" % (nokprobes, nprobes))
        print("")

        cls.on_disable()

        snapshots = cls.get_snapshots()
        if snapshots and snapshots[-1]:
            print(snapshots[-1].decode())

        super(ExplorationDebugger, cls).disable()

    @classmethod
    def get_snapshots(cls):
        # type: () -> t.List[Snapshot]
        if cls._instance is None:
            return None
        return cls._instance._collector.snapshots

    @classmethod
    def get_triggered_probes(cls):
        # type: () -> t.List[Probe]
        if cls._instance is None:
            return None
        return cls._instance._collector.probes

    @classmethod
    def add_probe(cls, probe):
        # type: (Probe) -> None
        cls._instance._on_configuration(ProbePollerEvent.NEW_PROBES, [probe])

    @classmethod
    def add_probes(cls, probes):
        # type: (t.List[Probe]) -> None
        cls._instance._on_configuration(ProbePollerEvent.NEW_PROBES, probes)

    @classmethod
    def delete_probe(cls, probe):
        # type: (Probe) -> None
        cls._instance._on_configuration(ProbePollerEvent.DELETED_PROBES, [probe])


if config.status_messages:

    def status(msg):
        # type: (str) -> None
        print(("{:%d}" % COLS).format(msg))


else:

    def status(msg):
        # type: (str) -> None
        pass
