import os
from pathlib import Path
import sys
from types import ModuleType
import typing as t

from _config import config
from output import log

from ddtrace.debugging._config import di_config
import ddtrace.debugging._debugger as _debugger
from ddtrace.debugging._debugger import Debugger
from ddtrace.debugging._debugger import DebuggerModuleWatchdog
from ddtrace.debugging._encoding import LogSignalJsonEncoder
from ddtrace.debugging._function.discovery import FunctionDiscovery
from ddtrace.debugging._probe.model import Probe
from ddtrace.debugging._probe.remoteconfig import ProbePollerEvent
from ddtrace.debugging._signal.collector import SignalCollector
from ddtrace.debugging._signal.snapshot import Snapshot
from ddtrace.internal.module import origin
from ddtrace.internal.remoteconfig.worker import RemoteConfigPoller


class NoopRemoteConfig(RemoteConfigPoller):
    def register(self, *args, **kwargs):
        pass


# Disable remote config as we don't need it for exploration tests
_debugger.remoteconfig_poller = NoopRemoteConfig()

try:
    COLS, _ = os.get_terminal_size()
except Exception:
    COLS = 80
CWD = Path.cwd()


# Taken from Python 3.9. This is not implemented in older versions of Python
def is_relative_to(self, other):
    """Return True if the path is relative to another path or False."""
    try:
        self.relative_to(other)
        return True
    except ValueError:
        return False


def from_editable_install(module: ModuleType) -> bool:
    o = origin(module)
    if o is None:
        return False
    return (
        is_relative_to(o, CWD)
        and not any(_.stem.startswith("test") for _ in o.parents)
        and (config.venv is None or not is_relative_to(o, config.venv))
    )


def is_included(module: ModuleType) -> bool:
    segments = module.__name__.split(".")
    for i in config.include:
        if i == segments[: len(i)]:
            return True
    return False


def is_ddtrace(module: ModuleType) -> bool:
    name = module.__name__
    return name == "ddtrace" or name.startswith("ddtrace.")


class ModuleCollector(DebuggerModuleWatchdog):
    def __init__(self, *args, **kwargs):
        super(ModuleCollector, self).__init__(*args, **kwargs)

        self._imported_modules: t.Set[str] = set()

    def on_collect(self, discovery: FunctionDiscovery) -> None:
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

    def after_import(self, module: ModuleType) -> None:
        name = module.__name__
        if name in self._imported_modules:
            return

        self._imported_modules.add(name)

        self._on_new_module(module)

        super(ModuleCollector, self).after_import(module)

        if config.elusive:
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


class NoopSnapshotJsonEncoder(LogSignalJsonEncoder):
    def encode(self, snapshot: Snapshot) -> bytes:
        return b""


class ExplorationSignalCollector(SignalCollector):
    def __init__(self, *args, **kwargs):
        super(ExplorationSignalCollector, self).__init__(*args, **kwargs)
        encoder_class = LogSignalJsonEncoder if config.encode else NoopSnapshotJsonEncoder
        self._encoder = encoder_class("exploration")
        self._encoder._encoders = {Snapshot: self._encoder}
        self._snapshots: t.List[bytes] = []
        self._probes = []
        self._failed_encoding = []
        self.on_snapshot = None

    def _enqueue(self, snapshot: Snapshot) -> None:
        if config.encode:
            try:
                self._snapshots.append(self._encoder.encode(snapshot))
            except Exception:
                self._failed_encoding.append(snapshot)

        self._probes.append(snapshot.probe)
        if self.on_snapshot:
            self.on_snapshot(snapshot)

    @property
    def snapshots(self) -> t.List[t.Optional[bytes]]:
        return self._snapshots or [None]

    @property
    def probes(self) -> t.List[t.Optional[Probe]]:
        return self._probes or [None]


class ExplorationDebugger(Debugger):
    __rc__ = NoopDebuggerRC
    __uploader__ = NoopLogsIntakeUploader
    __collector__ = ExplorationSignalCollector
    __watchdog__ = ModuleCollector
    __logger__ = NoopProbeStatusLogger
    __poller__ = NoopProbePoller

    @classmethod
    def on_disable(cls) -> None:
        raise NotImplementedError()

    @classmethod
    def on_snapshot(cls, snapshot: Snapshot) -> None:
        pass

    @classmethod
    def enable(cls) -> None:
        di_config.max_probes = float("inf")
        di_config.global_rate_limit = float("inf")
        di_config.metrics = False

        super(ExplorationDebugger, cls).enable()

        cls._instance._collector.on_snapshot = cls.on_snapshot

    @classmethod
    def disable(cls, join: bool = True) -> None:
        registry = cls._instance._probe_registry

        nprobes = len(registry)
        nokprobes = sum(_.installed for _ in registry.values())

        log(("{:=^%ds}" % COLS).format(" %s: probes stats " % cls.__name__))
        log("")

        log("Installed probes: %d/%d" % (nokprobes, nprobes))
        log("")

        cls.on_disable()

        snapshots = cls.get_snapshots()
        if snapshots and snapshots[-1] is not None:
            import json
            from pprint import pprint

            pprint(json.loads(snapshots[-1].decode()), stream=config.output_stream)

        super(ExplorationDebugger, cls).disable(join=join)

    @classmethod
    def get_snapshots(cls) -> t.List[t.Optional[bytes]]:
        if cls._instance is None:
            return None
        return cls._instance._collector.snapshots

    @classmethod
    def get_triggered_probes(cls) -> t.List[Probe]:
        if cls._instance is None:
            return None
        return cls._instance._collector.probes

    @classmethod
    def add_probe(cls, probe: Probe) -> None:
        cls._instance._on_configuration(ProbePollerEvent.NEW_PROBES, [probe])

    @classmethod
    def add_probes(cls, probes: t.List[Probe]) -> None:
        cls._instance._on_configuration(ProbePollerEvent.NEW_PROBES, probes)

    @classmethod
    def delete_probe(cls, probe: Probe) -> None:
        cls._instance._on_configuration(ProbePollerEvent.DELETED_PROBES, [probe])


if config.status_messages:

    def status(msg: str) -> None:
        log(("{:%d}" % COLS).format(msg))

else:

    def status(msg: str) -> None:
        pass
