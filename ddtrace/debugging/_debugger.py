from collections import defaultdict
from itertools import chain
import os
import sys
import threading
from types import FunctionType
from types import ModuleType
from typing import Any
from typing import Dict
from typing import Iterable
from typing import List
from typing import Optional
from typing import Set
from typing import TYPE_CHECKING
from typing import Tuple
from typing import cast

from six import PY3

import ddtrace
from ddtrace.debugging._capture.collector import CapturedEventCollector
from ddtrace.debugging._capture.metric_sample import MetricSample
from ddtrace.debugging._capture.model import CapturedEvent
from ddtrace.debugging._capture.snapshot import Snapshot
from ddtrace.debugging._capture.tracing import DynamicSpan
from ddtrace.debugging._config import config
from ddtrace.debugging._encoding import BatchJsonEncoder
from ddtrace.debugging._encoding import SnapshotJsonEncoder
from ddtrace.debugging._function.discovery import FunctionDiscovery
from ddtrace.debugging._function.store import FullyNamedWrappedFunction
from ddtrace.debugging._function.store import FunctionStore
from ddtrace.debugging._metrics import metrics
from ddtrace.debugging._probe.model import FunctionLocationMixin
from ddtrace.debugging._probe.model import FunctionProbe
from ddtrace.debugging._probe.model import LineLocationMixin
from ddtrace.debugging._probe.model import LineProbe
from ddtrace.debugging._probe.model import LogFunctionProbe
from ddtrace.debugging._probe.model import LogLineProbe
from ddtrace.debugging._probe.model import MetricFunctionProbe
from ddtrace.debugging._probe.model import MetricLineProbe
from ddtrace.debugging._probe.model import Probe
from ddtrace.debugging._probe.model import SpanFunctionProbe
from ddtrace.debugging._probe.registry import ProbeRegistry
from ddtrace.debugging._probe.remoteconfig import ProbePollerEvent
from ddtrace.debugging._probe.remoteconfig import ProbePollerEventType
from ddtrace.debugging._probe.remoteconfig import ProbeRCAdapter
from ddtrace.debugging._probe.status import ProbeStatusLogger
from ddtrace.debugging._uploader import LogsIntakeUploaderV1
from ddtrace.internal import atexit
from ddtrace.internal import compat
from ddtrace.internal import forksafe
from ddtrace.internal.logger import get_logger
from ddtrace.internal.metrics import Metrics
from ddtrace.internal.module import ModuleHookType
from ddtrace.internal.module import ModuleWatchdog
from ddtrace.internal.module import origin
from ddtrace.internal.module import register_post_run_module_hook
from ddtrace.internal.module import unregister_post_run_module_hook
from ddtrace.internal.rate_limiter import BudgetRateLimiterWithJitter as RateLimiter
from ddtrace.internal.rate_limiter import RateLimitExceeded
from ddtrace.internal.remoteconfig import RemoteConfig
from ddtrace.internal.safety import _isinstance
from ddtrace.internal.service import Service
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.wrapping import Wrapper


if TYPE_CHECKING:  # pragma: no cover
    from ddtrace.tracer import Tracer


# Coroutine support
if PY3:
    from types import CoroutineType

    from ddtrace.debugging._async import dd_coroutine_wrapper
else:
    CoroutineType = dd_coroutine_wrapper = None

log = get_logger(__name__)

_probe_metrics = Metrics(namespace="dynamic.instrumentation.metric")
_probe_metrics.enable()


class DebuggerError(Exception):
    """Generic debugger error."""

    pass


class DebuggerModuleWatchdog(ModuleWatchdog):
    _locations = set()  # type: Set[str]

    @classmethod
    def register_origin_hook(cls, origin, hook):
        # type: (str, ModuleHookType) -> None
        if origin in cls._locations:
            # We already have a hook for this origin, don't register a new one
            # but invoke it directly instead, if the module was already loaded.
            module = cls.get_by_origin(origin)
            if module is not None:
                hook(module)

            return

        cls._locations.add(origin)

        super(DebuggerModuleWatchdog, cls).register_origin_hook(origin, hook)

    @classmethod
    def unregister_origin_hook(cls, origin, hook):
        # type: (str, ModuleHookType) -> None
        try:
            cls._locations.remove(origin)
        except KeyError:
            # Nothing to unregister.
            return

        return super(DebuggerModuleWatchdog, cls).unregister_origin_hook(origin, hook)

    @classmethod
    def register_module_hook(cls, module_name, hook):
        # type: (str, ModuleHookType) -> None
        if module_name in cls._locations:
            # We already have a hook for this origin, don't register a new one
            # but invoke it directly instead, if the module was already loaded.
            module = sys.modules[module_name]
            if module is not None:
                hook(module)

            return

        cls._locations.add(module_name)

        super(DebuggerModuleWatchdog, cls).register_module_hook(module_name, hook)

    @classmethod
    def unregister_module_hook(cls, module_name, hook):
        # type: (str, ModuleHookType) -> None
        try:
            cls._locations.remove(module_name)
        except KeyError:
            # Nothing to unregister.
            return

        return super(DebuggerModuleWatchdog, cls).unregister_module_hook(module_name, hook)

    @classmethod
    def on_run_module(cls, module):
        # type: (ModuleType) -> None
        if cls._instance is not None:
            # Treat run module as an import to trigger import hooks and register
            # the module's origin.
            cls._instance.after_import(module)


class Debugger(Service):
    _instance = None  # type: Optional[Debugger]
    _probe_meter = _probe_metrics.get_meter("probe")

    __rc_adapter__ = ProbeRCAdapter
    __uploader__ = LogsIntakeUploaderV1
    __collector__ = CapturedEventCollector
    __watchdog__ = DebuggerModuleWatchdog
    __logger__ = ProbeStatusLogger

    @classmethod
    def enable(cls, run_module=False):
        # type: (bool) -> None
        """Enable dynamic instrumentation

        This class method is idempotent. Dynamic instrumentation will be
        disabled automatically at exit.
        """
        if cls._instance is not None:
            log.debug("%s already enabled", cls.__name__)
            return

        log.debug("Enabling %s", cls.__name__)

        cls.__watchdog__.install()

        if config.metrics:
            metrics.enable()

        cls._instance = debugger = cls()

        debugger.start()

        forksafe.register(cls._restart)
        atexit.register(cls.disable)
        register_post_run_module_hook(cls._on_run_module)

        log.debug("%s enabled", cls.__name__)

    @classmethod
    def disable(cls, join=True):
        # type: (bool) -> None
        """Disable dynamic instrumentation.

        This class method is idempotent. Called automatically at exit, if
        dynamic instrumentation was enabled.
        """
        if cls._instance is None:
            log.debug("%s not enabled", cls.__name__)
            return

        log.debug("Disabling %s", cls.__name__)

        forksafe.unregister(cls._restart)
        atexit.unregister(cls.disable)
        unregister_post_run_module_hook(cls._on_run_module)

        cls._instance.stop(join=join)
        cls._instance = None

        cls.__watchdog__.uninstall()
        if config.metrics:
            metrics.disable()

        log.debug("%s disabled", cls.__name__)

    def __init__(self, tracer=None):
        # type: (Optional[Tracer]) -> None
        super(Debugger, self).__init__()

        self._tracer = tracer or ddtrace.tracer
        service_name = config.service_name

        self._encoder = BatchJsonEncoder(
            item_encoders={
                Snapshot: SnapshotJsonEncoder(service_name),
                str: str,
            },
            on_full=self._on_encoder_buffer_full,
        )
        self._probe_registry = ProbeRegistry(self.__logger__(service_name, self._encoder))
        self._uploader = self.__uploader__(self._encoder)
        self._collector = self.__collector__(self._encoder)
        self._services = [self._uploader]

        self._function_store = FunctionStore(extra_attrs=["__dd_wrappers__"])

        log_limiter = RateLimiter(limit_rate=1.0, raise_on_exceed=False)
        self._global_rate_limiter = RateLimiter(
            limit_rate=config.global_rate_limit,  # TODO: Make it configurable. Note that this is per-process!
            on_exceed=lambda: log_limiter.limit(log.warning, "Global rate limit exceeded"),
            call_once=True,
            raise_on_exceed=False,
        )

        # TODO: this is only temporary and will be reverted once the DD_REMOTE_CONFIGURATION_ENABLED variable
        #  has been removed
        if asbool(os.environ.get("DD_REMOTE_CONFIGURATION_ENABLED", True)) is False:
            os.environ["DD_REMOTE_CONFIGURATION_ENABLED"] = "true"
            log.info("Disabled Remote Configuration enabled by Dynamic Instrumentation.")

        # Register the debugger with the RCM client.
        RemoteConfig.register("LIVE_DEBUGGING", self.__rc_adapter__(self._on_configuration))

        log.debug("%s initialized (service name: %s)", self.__class__.__name__, service_name)

    def _on_encoder_buffer_full(self, item, encoded):
        # type (Any, bytes) -> None
        # Send upload request
        self._uploader.upload()

    def _dd_debugger_hook(self, probe):
        # type: (Probe) -> None
        """Debugger probe hook.

        This gets called with a reference to the probe. We only check whether
        the probe is active. If so, we push the collected data to the collector
        for bulk processing. This way we avoid adding delay while the
        instrumented code is running.
        """
        try:
            actual_frame = sys._getframe(1)
            event = None  # type: Optional[CapturedEvent]
            if isinstance(probe, MetricLineProbe):
                event = MetricSample(
                    probe=probe,
                    frame=actual_frame,
                    thread=threading.current_thread(),
                    context=self._tracer.current_trace_context(),
                    meter=self._probe_meter,
                )
            elif isinstance(probe, LogLineProbe):
                if probe.take_snapshot:
                    # TODO: Global limit evaluated before probe conditions
                    if self._global_rate_limiter.limit() is RateLimitExceeded:
                        return

                event = Snapshot(
                    probe=probe,
                    frame=actual_frame,
                    thread=threading.current_thread(),
                    context=self._tracer.current_trace_context(),
                )
            else:
                log.error("Unsupported probe type: %r", type(probe))
                return

            event.line()

            self._collector.push(event)

        except Exception:
            log.error("Failed to execute debugger probe hook", exc_info=True)

    def _dd_debugger_wrapper(self, wrappers):
        # type: (Dict[str, FunctionProbe]) -> Wrapper
        """Debugger wrapper.

        This gets called with a reference to the wrapped function and the probe,
        together with the arguments to pass. We only check
        whether the probe is active and the debugger is enabled. If so, we
        capture all the relevant debugging context.
        """

        def _(wrapped, args, kwargs):
            # type: (FunctionType, Tuple[Any], Dict[str,Any]) -> Any
            if not wrappers:
                return wrapped(*args, **kwargs)

            argnames = wrapped.__code__.co_varnames
            actual_frame = sys._getframe(1)
            allargs = list(chain(zip(argnames, args), kwargs.items()))
            thread = threading.current_thread()
            trace_context = self._tracer.current_trace_context()

            open_contexts = []
            event = None  # type: Optional[CapturedEvent]
            for probe in wrappers.values():
                if isinstance(probe, MetricFunctionProbe):
                    event = MetricSample(
                        probe=probe,
                        frame=actual_frame,
                        thread=thread,
                        args=allargs,
                        context=trace_context,
                        meter=self._probe_meter,
                    )
                elif isinstance(probe, LogFunctionProbe):
                    event = Snapshot(
                        probe=probe,
                        frame=actual_frame,
                        thread=thread,
                        args=allargs,
                        context=trace_context,
                    )
                elif isinstance(probe, SpanFunctionProbe):
                    event = DynamicSpan(
                        probe=probe,
                        frame=actual_frame,
                        thread=thread,
                        args=allargs,
                        context=trace_context,
                    )
                else:
                    log.error("Unsupported probe type: %s", type(probe))
                    continue

                open_contexts.append(self._collector.attach(event))

            if not open_contexts:
                return wrapped(*args, **kwargs)

            start_time = compat.monotonic_ns()
            try:
                retval = wrapped(*args, **kwargs)
                end_time = compat.monotonic_ns()
                exc_info = (None, None, None)
            except Exception:
                end_time = compat.monotonic_ns()
                retval = None
                exc_info = sys.exc_info()  # type: ignore[assignment]
            else:
                # DEV: We do not unwind generators here as they might result in
                # tight loops. We return the result as a generator object
                # instead.
                if PY3 and _isinstance(retval, CoroutineType):
                    return dd_coroutine_wrapper(retval, open_contexts)

            for context in open_contexts:
                context.exit(retval, exc_info, end_time - start_time)

            exc = exc_info[1]
            if exc is not None:
                raise exc

            return retval

        return _

    def _probe_injection_hook(self, module):
        # type: (ModuleType) -> None
        # This hook is invoked by the ModuleWatchdog or the post run module hook
        # to inject probes.

        # Group probes by function so that we decompile each function once and
        # bulk-inject the probes.
        probes_for_function = defaultdict(list)  # type: Dict[FullyNamedWrappedFunction, List[Probe]]
        for probe in self._probe_registry.get_pending(origin(module)):
            if not isinstance(probe, LineLocationMixin):
                continue
            line = probe.line
            assert line is not None
            functions = FunctionDiscovery.from_module(module).at_line(line)
            if not functions:
                message = "Cannot inject probe %s: no functions at line %d within source file %s" % (
                    probe.probe_id,
                    line,
                    origin(module),
                )
                log.error(message)
                self._probe_registry.set_error(probe, message)
                continue
            for function in (cast(FullyNamedWrappedFunction, _) for _ in functions):
                probes_for_function[function].append(cast(LineProbe, probe))

        for function, probes in probes_for_function.items():
            failed = self._function_store.inject_hooks(
                function, [(self._dd_debugger_hook, cast(LineProbe, probe).line, probe) for probe in probes]
            )
            for probe in probes:
                if probe.probe_id in failed:
                    self._probe_registry.set_error(probe, "Failed to inject")
                    log.error("Failed to inject %r", probe)
                else:
                    self._probe_registry.set_installed(probe)
                    log.debug("Injected probes %r in %r", [probe.probe_id for probe in probes], function)

    def _inject_probes(self, probes):
        # type: (List[LineProbe]) -> None
        for probe in probes:
            if probe not in self._probe_registry:
                log.debug("Received new %s.", probe)
                self._probe_registry.register(probe)

            resolved_source = probe.source_file
            if resolved_source is None:
                log.error(
                    "Cannot inject probe %s: source file %s cannot be resolved", probe.probe_id, probe.source_file
                )
                self._probe_registry.set_error(probe, "Source file location cannot be resolved")
                continue

        for source in {probe.source_file for probe in probes if probe.source_file is not None}:
            try:
                self.__watchdog__.register_origin_hook(source, self._probe_injection_hook)
            except Exception:
                exc_info = sys.exc_info()
                for probe in probes:
                    if probe.source_file != source:
                        continue
                    self._probe_registry.set_exc_info(probe, exc_info)
                log.error("Cannot register probe injection hook on source '%s'", source, exc_info=True)

    def _eject_probes(self, probes_to_eject):
        # type: (List[LineProbe]) -> None
        # TODO[perf]: Bulk-collect probes as for injection. This is lower
        # priority as probes are normally removed manually by users.
        unregistered_probes = []  # type: List[LineProbe]
        for probe in probes_to_eject:
            if probe not in self._probe_registry:
                log.error("Attempted to eject unregistered probe %r", probe)
                continue

            (registered_probe,) = self._probe_registry.unregister(probe)
            unregistered_probes.append(cast(LineProbe, registered_probe))

        probes_for_source = defaultdict(list)  # type: Dict[str, List[LineProbe]]
        for probe in unregistered_probes:
            if probe.source_file is None:
                continue
            probes_for_source[probe.source_file].append(probe)

        for resolved_source, probes in probes_for_source.items():
            module = self.__watchdog__.get_by_origin(resolved_source)
            if module is not None:
                # The module is still loaded, so we can try to eject the hooks
                probes_for_function = defaultdict(list)  # type: Dict[FullyNamedWrappedFunction, List[LineProbe]]
                for probe in probes:
                    if not isinstance(probe, LineLocationMixin):
                        continue
                    line = probe.line
                    assert line is not None, probe
                    functions = FunctionDiscovery.from_module(module).at_line(line)
                    for function in (cast(FullyNamedWrappedFunction, _) for _ in functions):
                        probes_for_function[function].append(probe)

                for function, ps in probes_for_function.items():
                    failed = self._function_store.eject_hooks(
                        cast(FunctionType, function),
                        [(self._dd_debugger_hook, probe.line, probe) for probe in ps if probe.line is not None],
                    )
                    for probe in ps:
                        if probe.probe_id in failed:
                            log.error("Failed to eject %r from %r", probe, function)
                        else:
                            log.debug("Ejected %r from %r", probe, function)

            if not self._probe_registry.has_probes(resolved_source):
                try:
                    self.__watchdog__.unregister_origin_hook(resolved_source, self._probe_injection_hook)
                    log.debug("Unregistered injection hook on source '%s'", resolved_source)
                except ValueError:
                    log.error("Cannot unregister injection hook for %r", probe, exc_info=True)

    def _probe_wrapping_hook(self, module):
        # type: (ModuleType) -> None
        probes = self._probe_registry.get_pending(module.__name__)
        for probe in probes:
            if not isinstance(probe, FunctionLocationMixin):
                continue

            assert probe.module == module.__name__, "Imported module name matches probe definition"

            try:
                assert probe.module is not None and probe.func_qname is not None
                function = FunctionDiscovery.from_module(module).by_name(probe.func_qname)
            except ValueError:
                message = "Cannot inject probe %s: no function '%s' in module %s" % (
                    probe.probe_id,
                    probe.func_qname,
                    probe.module,
                )
                self._probe_registry.set_error(probe, message)
                log.error(message)
                continue

            if hasattr(function, "__dd_wrappers__"):
                # TODO: Check if this can be made into a set instead
                wrapper = cast(FullyNamedWrappedFunction, function)
                assert wrapper.__dd_wrappers__, "Function has debugger wrappers"
                wrapper.__dd_wrappers__[probe.probe_id] = probe
                log.debug("Function probe %r added to already wrapped %r", probe.probe_id, function)
            else:
                wrappers = cast(FullyNamedWrappedFunction, function).__dd_wrappers__ = {probe.probe_id: probe}
                self._function_store.wrap(cast(FunctionType, function), self._dd_debugger_wrapper(wrappers))
                log.debug("Function probe %r wrapped around %r", probe.probe_id, function)
            self._probe_registry.set_installed(probe)

    def _wrap_functions(self, probes):
        # type: (List[FunctionProbe]) -> None
        for probe in probes:
            self._probe_registry.register(probe)
            try:
                assert probe.module is not None
                self.__watchdog__.register_module_hook(probe.module, self._probe_wrapping_hook)
            except Exception:
                self._probe_registry.set_exc_info(probe, sys.exc_info())
                log.error("Cannot register probe wrapping hook on module '%s'", probe.module, exc_info=True)

    def _unwrap_functions(self, probes):
        # type: (List[FunctionProbe]) -> None

        # Keep track of all the modules involved to see if there are any import
        # hooks that we can clean up at the end.
        touched_modules = set()  # type: Set[str]

        for probe in probes:
            registered_probes = self._probe_registry.unregister(probe)
            if not registered_probes:
                log.error("Attempted to eject unregistered probe %r", probe)
                continue

            (registered_probe,) = registered_probes

            assert probe.module is not None
            module = sys.modules.get(probe.module, None)
            if module is not None:
                # The module is still loaded, so we can try to unwrap the function
                touched_modules.add(probe.module)
                assert probe.func_qname is not None
                function = FunctionDiscovery.from_module(module).by_name(probe.func_qname)
                if hasattr(function, "__dd_wrappers__"):
                    wrapper = cast(FullyNamedWrappedFunction, function)
                    assert wrapper.__dd_wrappers__, "Function has debugger wrappers"
                    del wrapper.__dd_wrappers__[probe.probe_id]
                    if not wrapper.__dd_wrappers__:
                        del wrapper.__dd_wrappers__
                        self._function_store.unwrap(wrapper)
                    log.debug("Unwrapped %r", registered_probe)
                else:
                    log.error("Attempted to unwrap %r, but no wrapper found", registered_probe)

        # Clean up import hooks.
        for module_name in touched_modules:
            if not self._probe_registry.has_probes(module_name):
                try:
                    self.__watchdog__.unregister_module_hook(module_name, self._probe_wrapping_hook)
                    log.debug("Unregistered wrapping import hook on module %s", module_name)
                except ValueError:
                    log.error("Cannot unregister wrapping import hook for module %r", module_name, exc_info=True)

    def _on_configuration(self, event, probes):
        # type: (ProbePollerEventType, Iterable[Probe]) -> None
        log.debug("Received poller event %r with probes %r", event, probes)
        if len(list(probes)) + len(self._probe_registry) > config.max_probes:
            log.warning("Too many active probes. Ignoring new ones.")
            return

        if event == ProbePollerEvent.STATUS_UPDATE:
            self._probe_registry.log_probes_status()
            return

        if event == ProbePollerEvent.MODIFIED_PROBES:
            for probe in probes:
                if probe in self._probe_registry:
                    registered_probe = self._probe_registry.get(probe.probe_id)
                    if registered_probe is None:
                        # We didn't have the probe. This shouldn't have happened!
                        log.error("Modified probe %r was not found in registry.", probe)
                        continue
                    self._probe_registry.update(probe)

            return

        line_probes = []  # type: List[LineProbe]
        function_probes = []  # type: List[FunctionProbe]
        for probe in probes:
            if isinstance(probe, LineLocationMixin):
                line_probes.append(cast(LineProbe, probe))
            elif isinstance(probe, FunctionLocationMixin):
                function_probes.append(cast(FunctionProbe, probe))
            else:
                log.warning("Skipping probe '%r': not supported.", probe)

        if event == ProbePollerEvent.NEW_PROBES:
            self._inject_probes(line_probes)
            self._wrap_functions(function_probes)
        elif event == ProbePollerEvent.DELETED_PROBES:
            self._eject_probes(line_probes)
            self._unwrap_functions(function_probes)
        else:
            raise ValueError("Unknown probe poller event %r" % event)

    def _stop_service(self, join=True):
        # type: (bool) -> None
        self._function_store.restore_all()
        for service in self._services:
            service.stop()
            if join:
                service.join()

    def _start_service(self):
        # type: () -> None
        for service in self._services:
            service.start()

    @classmethod
    def _restart(cls):
        log.info("Restarting the debugger in child process")
        cls.disable(join=False)
        cls.enable()

    @classmethod
    def _on_run_module(cls, module):
        # type: (ModuleType) -> None
        debugger = cls._instance
        if debugger is not None:
            debugger.__watchdog__.on_run_module(module)
