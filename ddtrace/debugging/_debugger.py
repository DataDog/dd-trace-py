from collections import defaultdict
from collections import deque
from itertools import chain
import json
import linecache
import os
from pathlib import Path
import sys
import threading
import time
from types import FunctionType
from types import ModuleType
from types import TracebackType
from typing import Any
from typing import Iterable
from typing import Optional
from typing import TypeVar
from typing import cast

import ddtrace
from ddtrace import config as ddconfig
from ddtrace.debugging._config import di_config
from ddtrace.debugging._function.discovery import FunctionDiscovery
from ddtrace.debugging._function.store import FullyNamedContextWrappedFunction
from ddtrace.debugging._function.store import FunctionStore
from ddtrace.debugging._import import DebuggerModuleWatchdog
from ddtrace.debugging._metrics import metrics
from ddtrace.debugging._probe.model import FunctionLocationMixin
from ddtrace.debugging._probe.model import FunctionProbe
from ddtrace.debugging._probe.model import LineLocationMixin
from ddtrace.debugging._probe.model import LineProbe
from ddtrace.debugging._probe.model import Probe
from ddtrace.debugging._probe.registry import ProbeRegistry
from ddtrace.debugging._probe.remoteconfig import DebuggerRCCallback
from ddtrace.debugging._probe.remoteconfig import ProbePollerEvent
from ddtrace.debugging._probe.remoteconfig import build_probe
from ddtrace.debugging._probe.status import ProbeStatusLogger
from ddtrace.debugging._sampling import DebuggerSampler
from ddtrace.debugging._sampling import Decision
from ddtrace.debugging._signal.collector import SignalCollector
from ddtrace.debugging._signal.model import Signal
from ddtrace.debugging._signal.model import SignalState
from ddtrace.debugging._uploader import SignalUploader
from ddtrace.debugging._uploader import UploaderProduct
from ddtrace.internal import core
from ddtrace.internal.compat import NO_EXCEPTION
from ddtrace.internal.compat import ExcInfoType
from ddtrace.internal.logger import get_logger
from ddtrace.internal.metrics import DogStatsdClient
from ddtrace.internal.metrics import Metrics
from ddtrace.internal.module import origin
from ddtrace.internal.module import register_post_run_module_hook
from ddtrace.internal.module import unregister_post_run_module_hook
from ddtrace.internal.native import RemoteConfigProduct
from ddtrace.internal.rate_limiter import BudgetRateLimiterWithJitter as RateLimiter
from ddtrace.internal.remoteconfig.worker import remoteconfig_poller
from ddtrace.internal.service import Service
from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.wrapping.context import WrappingContext
from ddtrace.trace import Tracer


log = get_logger(__name__)

_probe_metrics = Metrics(client=DogStatsdClient(namespace="dynamic.instrumentation.metric"))
_probe_metrics.enable()

_sampling_meter = metrics.get_meter("sampling")

#: Guardrail metric reasons for firings the sampler turned away, using the same
#: vocabulary the signal collector reports for the drops it sees.
_SKIP_REASONS = {
    Decision.DROP_SAMPLED: "rateLimitGlobal",
    Decision.DROP_CAPPED: "budgetExceededInvocation",
    Decision.DROP_RATE: "rateLimitProbe",
}


def record_skipped(probe: Probe, decision: Decision) -> None:
    """Report a firing that the sampler turned away."""
    _sampling_meter.increment(
        "dynamic_instrumentation.guardrails.events.skipped",
        tags={"reason": _SKIP_REASONS[decision], "probe_type": type(probe).__name__},
    )


T = TypeVar("T")


class DebuggerError(Exception):
    """Generic debugger error."""

    pass


class DebuggerWrappingContext(WrappingContext):
    """Wraps a function that carries probes, of either kind.

    Every probe requires its enclosing function to be wrapped. Function probes
    are triggered from here, on entry and on exit. Line probes are triggered by
    hooks injected at their lines instead, but they still need the invocation
    bracketed, because that is what gives them an execution unit to share a
    sampling decision with. A line probe cannot bracket itself.
    """

    __priority__ = 99  # Execute after all other contexts

    def __init__(
        self,
        f: FunctionType,
        collector: SignalCollector,
        registry: ProbeRegistry,
        tracer: Tracer,
        probe_meter: Metrics.Meter,
        sampler: DebuggerSampler,
    ) -> None:
        super().__init__(f)

        self._collector = collector
        self._probe_registry = registry
        self._tracer = tracer
        self._probe_meter = probe_meter
        self._sampler = sampler

        # Kept apart because only the function probes are triggered from here.
        # The line probes are held so that we know the wrapping is still needed.
        self.function_probes: dict[str, Probe] = {}
        self.line_probes: dict[str, Probe] = {}

    def _probes_for(self, probe: Probe) -> dict[str, Probe]:
        return self.line_probes if isinstance(probe, LineLocationMixin) else self.function_probes

    def add_probe(self, probe: Probe) -> None:
        self._probes_for(probe)[probe.probe_id] = probe

    def remove_probe(self, probe: Probe) -> None:
        # Tolerant of probes that were never added, since ejection is best
        # effort: a probe whose injection failed never made it onto the context.
        self._probes_for(probe).pop(probe.probe_id, None)

    def has_probes(self) -> bool:
        return bool(self.function_probes or self.line_probes)

    def _open_signals(self) -> None:
        # Group probes on the basis of whether they create new context.
        context_creators: list[Probe] = []
        context_consumers: list[Probe] = []
        for p in self.function_probes.values():
            (context_creators if p.__context_creator__ else context_consumers).append(p)

        signals: deque[Signal] = deque()

        try:
            frame = self.__frame__
            thread = threading.current_thread()

            # Trigger the context creators first, so that the new context can be
            # consumed by the consumers.
            for probe in chain(context_creators, context_consumers):
                # Because new context might be created, we need to recompute it
                # for each probe.
                trace_context = self._tracer.current_trace_context()

                decision = self._sampler.evaluate(probe, frame, trace_context)
                if decision is not Decision.FIRE:
                    record_skipped(probe, decision)
                    continue

                try:
                    signal = Signal.from_probe(
                        probe,
                        frame=frame,
                        thread=thread,
                        trace_context=trace_context,
                        meter=self._probe_meter,
                    )
                except TypeError:
                    log.error("Unsupported probe type: %s", type(probe))
                    continue

                try:
                    signal.do_enter()
                except Exception as e:
                    telemetry_writer.add_error_log("Failed to enter signal", e)
                    continue
                signals.append(signal)
        finally:
            # Save state on the wrapping context
            self.set("start_time", time.monotonic_ns())
            self.set("signals", signals)

    def _close_signals(self, retval: Any = None, exc_info: tuple[Any, Any, Any] = NO_EXCEPTION) -> None:
        end_time = time.monotonic_ns()

        try:
            signals = cast("deque[Signal]", self.get("signals"))
        except KeyError as e:
            if self.function_probes:
                telemetry_writer.add_error_log("Signal contexts were not opened for function probe", e)
            # Otherwise this context is wrapped only to scope the line probes in
            # the function body, so there was nothing to open.
            return

        while signals:
            # Open probe signals are ordered, with those that have created new
            # tracing context first. We need to finalize them in reverse order,
            # so we pop them from the end of the queue (LIFO).
            signal = signals.pop()
            try:
                signal.do_exit(retval, exc_info, end_time - self.get("start_time"))
            except Exception as e:
                telemetry_writer.add_error_log("Failed to exit signal", e)
                continue

            self._collector.push(signal)
            if signal.state is SignalState.DONE:
                self._probe_registry.set_emitting(signal.probe)
                # The snapshot is on its way out, so account for it.
                self._sampler.account_for(signal.probe, signal.frame, signal.trace_context)

    def _close_scope(self) -> None:
        # Closed only once every signal has exited, so that probes evaluated on
        # exit still resolve against the same unit. The next invocation then gets
        # a fresh unit and a fresh decision.
        try:
            self._sampler.close_scope(self.get("scope_token"))
        except KeyError:
            log.error("Sampling scope was not opened for %r", self)

    def __enter__(self) -> "DebuggerWrappingContext":
        super().__enter__()

        # Open the unit of execution before any probe fires, so that every probe
        # within the invocation -- function probes here, and line probes in the
        # body -- shares a single sampling decision. This is the whole reason a
        # function with only line probes in it gets wrapped at all.
        self.set("scope_token", self._sampler.open_scope())

        # A function wrapped only to scope the line probes inside it has no
        # signals to open, and opening them is far from free.
        if self.function_probes:
            try:
                self._open_signals()
            except Exception:
                log.exception("Failed to open debugging contexts")

        return self

    def __return__(self, value: T) -> T:
        try:
            if self.function_probes:
                self._close_signals(retval=value)
        except Exception:
            log.exception("Failed to close debugging contexts from return")
        finally:
            self._close_scope()
        return super().__return__(value)

    def __exit__(
        self, exc_type: Optional[type[BaseException]], exc_val: Optional[BaseException], exc_tb: Optional[TracebackType]
    ) -> None:
        try:
            if self.function_probes:
                self._close_signals(exc_info=cast(ExcInfoType, (exc_type, exc_val, exc_tb)))
        except Exception:
            log.exception("Failed to close debugging contexts from exception block")
        finally:
            self._close_scope()
        super().__exit__(exc_type, exc_val, exc_tb)


class Debugger(Service):
    _instance: Optional["Debugger"] = None
    _probe_meter = _probe_metrics.get_meter("probe")

    __rc_adapter__ = DebuggerRCCallback
    __uploader__ = SignalUploader
    __watchdog__ = DebuggerModuleWatchdog
    __logger__ = ProbeStatusLogger

    @classmethod
    def enable(cls) -> None:
        """Enable dynamic instrumentation

        This class method is idempotent. Dynamic instrumentation will be
        disabled automatically at exit.
        """
        if cls._instance is not None:
            log.debug("%s already enabled", cls.__name__)
            return

        log.debug("Enabling %s", cls.__name__)

        di_config.enabled = True

        if di_config.metrics:
            metrics.enable()

        cls._instance = debugger = cls()

        debugger.start()

        register_post_run_module_hook(cls._on_run_module)

        log.debug("%s enabled", cls.__name__)

        core.dispatch("dynamic-instrumentation.enabled")

    @classmethod
    def disable(cls, join: bool = True) -> None:
        """Disable dynamic instrumentation.

        This class method is idempotent. Called automatically at exit, if
        dynamic instrumentation was enabled.
        """
        if cls._instance is None:
            log.debug("%s not enabled", cls.__name__)
            return

        log.debug("Disabling %s", cls.__name__)

        callback = remoteconfig_poller.get_registered(RemoteConfigProduct.LiveDebugging)

        remoteconfig_poller.unregister_callback(RemoteConfigProduct.LiveDebugging)
        remoteconfig_poller.disable_product(RemoteConfigProduct.LiveDebugging)

        # Currently the product enablement and the callback registration are
        # tied together within the RC client so here we have to pretend that
        # once we have disabled the debugger we also get an empty configuration
        # payload from RC.
        if callback is not None:
            cast(DebuggerRCCallback, callback).delete_all_probes()

        unregister_post_run_module_hook(cls._on_run_module)

        cls._instance.stop(join=join)
        cls._instance = None

        if di_config.metrics:
            metrics.disable()

        di_config.enabled = False

        log.debug("%s disabled", cls.__name__)

    def __init__(self, tracer: Optional[Tracer] = None) -> None:
        super().__init__()

        self._tracer = tracer or ddtrace.tracer
        service_name = di_config.service_name

        self._status_logger = status_logger = self.__logger__(service_name)

        self._probe_registry = ProbeRegistry(status_logger=status_logger)

        self._function_store = FunctionStore()

        # The ceiling the coordinated sampling decision is made against. Built
        # here, rather than at import, so that it reads the current config.
        log_limiter = RateLimiter(limit_rate=1.0, raise_on_exceed=False)
        self._sampler = DebuggerSampler(
            limit_rate=di_config.global_rate_limit,
            on_exceed=lambda: log_limiter.limit(log.warning, "Global rate limit exceeded"),
            call_once=True,
            raise_on_exceed=False,
        )

        self.probe_file = di_config.probe_file

        if di_config.enabled:
            # TODO: this is only temporary and will be reverted once the DD_REMOTE_CONFIGURATION_ENABLED variable
            #  has been removed
            if ddconfig._remote_config_enabled is False:
                ddconfig._remote_config_enabled = True
                log.info("Disabled Remote Configuration enabled by Dynamic Instrumentation.")

            # Register the debugger with the RCM client.
            # The callback handles periodic probe status emission internally
            di_callback = self.__rc_adapter__(
                self._on_configuration,
                status_logger,
                self._probe_registry,
                di_config.diagnostics_interval,
            )
            remoteconfig_poller.register_callback(RemoteConfigProduct.LiveDebugging, di_callback)
            remoteconfig_poller.enable_product(RemoteConfigProduct.LiveDebugging)

            # Load local probes from the probe file.
            self._load_local_config()

        log.debug("%s initialized (service name: %s)", self.__class__.__name__, service_name)

    def _load_local_config(self) -> None:
        if self.probe_file is None:
            return

        # This is intentionally an all or nothing approach. If one probe is malformed, none of the
        # local probes will be installed, that way waiting for the success log guarantees installation.
        try:
            raw_probes = json.loads(self.probe_file.read_text())

            probes = [build_probe(p) for p in raw_probes]

            self._on_configuration(ProbePollerEvent.NEW_PROBES, probes)
            log.info("Successfully loaded probes from file %s: %s", self.probe_file, [p.probe_id for p in probes])

        except Exception as e:
            log.error("Failed to load probes from file %s: %s", self.probe_file, e)

    def _dd_debugger_hook(self, probe: Probe) -> None:
        """Debugger probe hook.

        This gets called with a reference to the probe. We only check whether
        the probe is active. If so, we push the collected data to the collector
        for bulk processing. This way we avoid adding delay while the
        instrumented code is running.
        """
        try:
            trace_context = self._tracer.current_trace_context()

            frame = sys._getframe(1)

            decision = self._sampler.evaluate(probe, frame, trace_context)
            if decision is not Decision.FIRE:
                record_skipped(probe, decision)
                return

            try:
                signal = Signal.from_probe(
                    probe,
                    frame=frame,
                    thread=threading.current_thread(),
                    trace_context=trace_context,
                    meter=self._probe_meter,
                )
            except TypeError:
                log.error("Unsupported probe type: %r", type(probe), exc_info=True)
                return

            signal.do_line()

            if signal.state is SignalState.DONE:
                self._probe_registry.set_emitting(probe)
                # The snapshot is on its way out, so account for it.
                self._sampler.account_for(probe, frame, trace_context)

            log.debug("[%s][P: %s] Debugger. Report signal %s", os.getpid(), os.getppid(), signal)
            if (collector := self.__uploader__.get_collector()) is None:
                log.error("No collector available to push signal %s", signal)
                return

            collector.push(signal)

        except Exception:
            log.error("Failed to execute probe hook", exc_info=True)

    def _wrap(self, function: FunctionType, probe: Probe) -> bool:
        """Attach a probe to its enclosing function, wrapping it if needed.

        Every probe needs the function wrapped, whichever kind it is, so both
        instrumentation hooks come through here. There is at most one context per
        function, shared by the function probes on it and the line probes in it.

        Returns whether the probe could be attached.
        """
        if DebuggerWrappingContext.is_wrapped(function):
            context = cast(DebuggerWrappingContext, DebuggerWrappingContext.extract(function))
        else:
            collector = self.__uploader__.get_collector()
            if collector is None:
                log.error("No signal collector available")
                self._probe_registry.set_error(probe, "NoCollector", "No signal collector available")
                return False

            context = DebuggerWrappingContext(
                function,
                collector=collector,
                registry=self._probe_registry,
                tracer=self._tracer,
                probe_meter=self._probe_meter,
                sampler=self._sampler,
            )
            self._function_store.wrap(function, context)

        context.add_probe(probe)

        return True

    def _unwrap(self, function: FunctionType, probes: Iterable[Probe]) -> bool:
        """Detach probes from their enclosing function, unwrapping it if spent.

        Returns whether the function was wrapped to begin with.
        """
        if not DebuggerWrappingContext.is_wrapped(function):
            return False

        context = cast(DebuggerWrappingContext, DebuggerWrappingContext.extract(function))
        for probe in probes:
            context.remove_probe(probe)

        if not context.has_probes():
            self._function_store.unwrap(cast(FullyNamedContextWrappedFunction, function))

        return True

    def _probe_injection_hook(self, module: ModuleType) -> None:
        # This hook is invoked by the ModuleWatchdog or the post run module hook
        # to inject probes.

        # Group probes by function so that we decompile each function once and
        # bulk-inject the probes.
        probes_for_function: dict[FullyNamedContextWrappedFunction, list[Probe]] = defaultdict(list)
        for probe in self._probe_registry.get_pending(str(origin(module))):
            if not isinstance(probe, LineLocationMixin):
                continue
            line = probe.line
            assert line is not None  # nosec
            functions = FunctionDiscovery.from_module(module).at_line(line)
            if not functions:
                module_origin = str(origin(module))
                if linecache.getline(module_origin, line):
                    # The source actually has a line at the given line number
                    message = (
                        f"Cannot install probe {probe.probe_id}: "
                        f"function at line {line} within source file {module_origin} "
                        "is likely decorated with an unsupported decorator."
                    )
                else:
                    message = (
                        f"Cannot install probe {probe.probe_id}: "
                        f"no functions at line {line} within source file {module_origin} found"
                    )
                log.error(message, extra={"send_to_telemetry": False})
                self._probe_registry.set_error(probe, "NoFunctionsAtLine", message)
                continue
            for function in (cast(FullyNamedContextWrappedFunction, _) for _ in functions):
                probes_for_function[function].append(cast(LineProbe, probe))

        for function, probes in probes_for_function.items():
            failed = self._function_store.inject_hooks(
                function, [(self._dd_debugger_hook, cast(LineProbe, probe).line, probe) for probe in probes]
            )

            for probe in probes:
                if probe.probe_id in failed:
                    self._probe_registry.set_error(probe, "InjectionFailure", "Failed to inject")
                elif self._wrap(cast(FunctionType, function), probe):
                    self._probe_registry.set_installed(probe)

            if failed:
                log.error("[%s][P: %s] Failed to inject probes %r", os.getpid(), os.getppid(), failed)

            log.debug(
                "[%s][P: %s] Injected probes %r in %r",
                os.getpid(),
                os.getppid(),
                [probe.probe_id for probe in probes if probe.probe_id not in failed],
                function,
            )

    def _inject_probes(self, probes: list[LineProbe]) -> None:
        for probe in probes:
            if probe not in self._probe_registry:
                log.debug("[%s][P: %s] Received new %s.", os.getpid(), os.getppid(), probe)
                self._probe_registry.register(probe)

            resolved_source = probe.resolved_source_file
            if resolved_source is None:
                log.error(
                    "Cannot inject probe %s: source file %s cannot be resolved", probe.probe_id, probe.source_file
                )
                self._probe_registry.set_error(probe, "NoSourceFile", "Source file location cannot be resolved")
                continue

        for source in {probe.resolved_source_file for probe in probes if probe.resolved_source_file is not None}:
            try:
                self.__watchdog__.register_origin_hook(source, self._probe_injection_hook)
            except Exception as exc:
                for probe in probes:
                    if probe.resolved_source_file != source:
                        continue
                    exc_type = type(exc)
                    self._probe_registry.set_error(probe, exc_type.__name__, str(exc))
                log.error("Cannot register probe injection hook on source '%s'", source, exc_info=True)

    def _eject_probes(self, probes_to_eject: list[LineProbe]) -> None:
        # TODO[perf]: Bulk-collect probes as for injection. This is lower
        # priority as probes are normally removed manually by users.
        unregistered_probes: list[LineProbe] = []
        for probe in probes_to_eject:
            if probe not in self._probe_registry:
                log.error("Attempted to eject unregistered probe %r", probe)
                continue

            (registered_probe,) = self._probe_registry.unregister(probe)
            unregistered_probes.append(cast(LineProbe, registered_probe))

        probes_for_source: dict[Path, list[LineProbe]] = defaultdict(list)
        for probe in unregistered_probes:
            if probe.resolved_source_file is None:
                continue
            probes_for_source[probe.resolved_source_file].append(probe)

        for resolved_source, probes in probes_for_source.items():
            module = self.__watchdog__.get_by_origin(resolved_source)
            if module is not None:
                # The module is still loaded, so we can try to eject the hooks
                probes_for_function: dict[FullyNamedContextWrappedFunction, list[LineProbe]] = defaultdict(list)
                for probe in probes:
                    if not isinstance(probe, LineLocationMixin):
                        continue  # type: ignore[unreachable]
                    line = probe.line
                    assert line is not None, probe  # nosec
                    functions = FunctionDiscovery.from_module(module).at_line(line)
                    for function in (cast(FullyNamedContextWrappedFunction, _) for _ in functions):
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

                    self._unwrap(cast(FunctionType, function), ps)

            if not self._probe_registry.has_probes(str(resolved_source)):
                try:
                    self.__watchdog__.unregister_origin_hook(resolved_source, self._probe_injection_hook)
                    log.debug("Unregistered injection hook on source '%s'", resolved_source)
                except ValueError:
                    log.error("Cannot unregister injection hook on %r", resolved_source, exc_info=True)

    def _probe_wrapping_hook(self, module: ModuleType) -> None:
        probes = self._probe_registry.get_pending(module.__name__)
        for probe in probes:
            if not isinstance(probe, FunctionLocationMixin):
                continue

            try:
                assert probe.module is not None and probe.func_qname is not None  # nosec
                function = cast(FunctionType, FunctionDiscovery.from_module(module).by_name(probe.func_qname))
            except ValueError:
                message = (
                    f"Cannot install probe {probe.probe_id}: no function '{probe.func_qname}' in module {probe.module}"
                    "found (note: if the function exists, it might be decorated with an unsupported decorator)"
                )
                self._probe_registry.set_error(probe, "NoFunctionInModule", message)
                log.error(message, extra={"send_to_telemetry": False})
                continue

            already_wrapped = DebuggerWrappingContext.is_wrapped(function)
            if not self._wrap(function, probe):
                continue

            log.debug(
                "[%s][P: %s] Function probe %r %s %r",
                os.getpid(),
                os.getppid(),
                probe.probe_id,
                "added to already wrapped" if already_wrapped else "wrapped around",
                function,
            )

            self._probe_registry.set_installed(probe)

    def _wrap_functions(self, probes: list[FunctionProbe]) -> None:
        for probe in probes:
            self._probe_registry.register(probe)
            try:
                assert probe.module is not None  # nosec
                self.__watchdog__.register_module_hook(probe.module, self._probe_wrapping_hook)
            except Exception as exc:
                exc_type = type(exc)
                self._probe_registry.set_error(probe, exc_type.__name__, str(exc))
                log.error("Cannot register probe wrapping hook on module '%s'", probe.module, exc_info=True)

    def _unwrap_functions(self, probes: list[FunctionProbe]) -> None:
        # Keep track of all the modules involved to see if there are any import
        # hooks that we can clean up at the end.
        touched_modules: set[str] = set()

        for probe in probes:
            registered_probes = self._probe_registry.unregister(probe)
            if not registered_probes:
                log.error("Attempted to eject unregistered probe %r", probe)
                continue

            (registered_probe,) = registered_probes

            assert probe.module is not None  # nosec
            module = sys.modules.get(probe.module, None)
            if module is not None:
                # The module is still loaded, so we can try to unwrap the function
                touched_modules.add(probe.module)
                assert probe.func_qname is not None  # nosec
                function = cast(FunctionType, FunctionDiscovery.from_module(module).by_name(probe.func_qname))
                if self._unwrap(function, (probe,)):
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

    def _on_configuration(self, event: ProbePollerEvent, probes: Iterable[Probe]) -> None:
        log.debug("[%s][P: %s] Received poller event %r with probes %r", os.getpid(), os.getppid(), event, probes)

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

        line_probes: list[LineProbe] = []
        function_probes: list[FunctionProbe] = []
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

    def _stop_service(self, join: bool = True) -> None:
        self._function_store.restore_all()
        self.__uploader__.unregister(UploaderProduct.DEBUGGER)

    def _start_service(self) -> None:
        self.__uploader__.register(UploaderProduct.DEBUGGER)

    @classmethod
    def _on_run_module(cls, module: ModuleType) -> None:
        debugger = cls._instance
        if debugger is not None:
            debugger.__watchdog__.on_run_module(module)
