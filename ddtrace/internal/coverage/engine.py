"""Coverage instrumentation engines.

Each supported Python version family implements the same :class:`CoverageEngine` interface,
so the coverage collector (code.py) stays instrumentation-agnostic: version and mode
quirks — sys.monitoring vs bytecode traps, file-level probes, empty-module handling, event
re-arming — all live here instead of as conditionals in the collector.

Engines mirror the instrumentation module ladder:
    - ``SysMonitoringEngine``    Python 3.12+  (instrumentation_py3_12 / instrumentation_py3_16)
    - ``BytecodeInjectionEngine`` Python 3.10/3.11  (instrumentation_py3_10)
    - ``LegacyBytecodeEngine``   Python 3.9  (instrumentation_py3_9)
"""

import sys
from types import CodeType
import typing as t

from ddtrace.internal.compat import is_at_least_py
from ddtrace.internal.coverage.coverage_lines import CoverageLines


class CoverageCollector(t.Protocol):
    """Structural interface of the coverage collector, as used by the engines.

    This decouples engine.py from the concrete collector (ddtrace.internal.coverage.code),
    which imports engine.py — a one-way dependency: code -> engine. Implemented by
    ``ModuleCodeCollector``.
    """

    def hook(self, arg: tuple[int, str, t.Optional[tuple[str, tuple[str, ...]]]]) -> None: ...

    def hook_file(self, path: str) -> None: ...

    def hook_import(self, path: str, import_name: tuple[str, tuple[str, ...]]) -> None: ...


# The version-specific instrumentation modules assert their Python version at import time, so
# exactly one implementation is imported for the running interpreter — the same ladder the old
# instrumentation dispatch used. Importing them here keeps per-call imports out of the
# instrument()/arm() paths, which run for every code object.
if sys.version_info >= (3, 12):
    from ddtrace.internal.coverage.instrumentation_py3_12 import update_disable_optimization

    if sys.version_info >= (3, 16):
        from ddtrace.internal.coverage.instrumentation_py3_16 import instrument_all_lines
    else:
        from ddtrace.internal.coverage.instrumentation_py3_12 import instrument_all_lines
elif sys.version_info >= (3, 10):
    from ddtrace.internal.bytecode_injection.core import Probe as _Probe
    from ddtrace.internal.coverage.instrumentation_py3_10 import USE_FILE_LEVEL_COVERAGE
    from ddtrace.internal.coverage.instrumentation_py3_10 import instrument_all_lines
else:
    from ddtrace.internal.coverage.instrumentation_py3_9 import instrument_all_lines


class Probe(t.Protocol):
    """Protocol for the mutable per-site records carried by file-level bytecode traps.

    Implemented by ``ddtrace.internal.bytecode_injection.core.Probe``; the engine owns the
    dead-flag lifecycle (first fire per coverage context) and re-arms between contexts.
    """

    path: str
    deps: list[tuple[str, tuple[str, ...]]]
    dead: bool


class CoverageEngine:
    """Uniform instrumentation interface implemented for every supported Python version."""

    def __init__(self, collector: "CoverageCollector") -> None:
        self._collector = collector

    def instrument(self, code: CodeType, path: str, package: str) -> tuple[CodeType, CoverageLines]:
        """Inject coverage instrumentation into a code object.

        Returns the instrumented code object and the lines that can be covered (empty in
        file-level mode, where line bookkeeping is not used).
        """
        raise NotImplementedError

    def arm(self, import_context: bool = False) -> None:
        """Re-arm instrumentation for a new coverage context.

        This is the sys.monitoring ``restart_events()`` equivalent for engines whose events
        self-disable after first fire. ``import_context`` marks per-module-import contexts,
        whose module bodies execute exactly once; engines may skip re-arming for them.
        """
        return None

    def on_empty_module(self, code: CodeType, package: str) -> None:
        """Record coverage for empty modules, where needed. Default: nothing to do."""
        return None


# Bytecode for an empty module — used to detect empty __init__.py files (Python 3.13+ only
# fires the direct hook below because sys.monitoring does not fire LINE events for them).
_EMPTY_MODULE_BYTES = compile("", "<empty>", "exec").co_code


class SysMonitoringEngine(CoverageEngine):
    """Python 3.12+: sys.monitoring (PY_START/LINE) events, no bytecode rewriting."""

    def instrument(self, code: CodeType, path: str, package: str) -> tuple[CodeType, CoverageLines]:
        return instrument_all_lines(code, self._collector.hook, path, package)

    def arm(self, import_context: bool = False) -> None:
        # Dynamically detect whether other sys.monitoring tools are active and update the
        # DISABLE optimisation flag accordingly. Then re-enable monitoring only when the
        # optimisation is active (restart_events is global and would corrupt other tools'
        # state if called unnecessarily).
        # NOTE: This global restart_events() call is safe specifically because it is gated on
        # update_disable_optimization() having *just* confirmed no other tool is registered
        # -- there is no other tool's disabled-event state for it to corrupt at this instant.
        # Once another tool is detected, this branch stops firing for the rest of the run.
        # This is deliberately different from the tool-scoped set_local_events() toggle in
        # instrumentation_py3_12._rearm_all_events(), which handles the one case where a
        # *known* other tool is present and must be left untouched.
        # update_disable_optimization is imported for 3.12+ only (version-gated ladder above);
        # mypy lints under a 3.10 interpreter, where that import branch is unreachable.
        if update_disable_optimization():  # type: ignore[name-defined]
            sys.monitoring.restart_events()  # type: ignore[attr-defined]

    def on_empty_module(self, code: CodeType, package: str) -> None:
        # Python 3.13+ doesn't fire sys.monitoring LINE events for empty modules. Call the
        # hook directly so coverage is recorded (the line-0 sentinel routes to file-level
        # recording in file-level mode).
        if is_at_least_py(3, 13) and code.co_code == _EMPTY_MODULE_BYTES:
            import_info = (package, ("",)) if package else None
            self._collector.hook((0, code.co_filename, import_info))


class BytecodeInjectionEngine(CoverageEngine):
    """Python 3.10/3.11: bytecode traps via the shared bytecode-injection framework.

    In line-level mode traps emit ``(line, path, import_name)`` tuples handled by
    ``ModuleCodeCollector.hook()``. In file-level mode traps carry mutable :class:`Probe`
    records instead; this engine adapts them to the collector's fine-grained hook API and owns
    their re-arming, so probes never reach the collector directly.
    """

    def __init__(self, collector: "CoverageCollector") -> None:
        super().__init__(collector)
        self._probes: list[Probe] = []

    def instrument(self, code: CodeType, path: str, package: str) -> tuple[CodeType, CoverageLines]:
        if USE_FILE_LEVEL_COVERAGE:
            new_code, lines = instrument_all_lines(code, self._fire_probe, path, package)
            self._register_probes(new_code)
        else:
            new_code, lines = instrument_all_lines(code, self._collector.hook, path, package)

        return new_code, lines

    def _fire_probe(self, probe: Probe) -> None:
        # First fire per coverage context records the file and its import dependencies;
        # subsequent fires are a cheap dead-flag check. Re-armed by arm().
        if probe.dead:
            return
        probe.dead = True
        collector = self._collector
        collector.hook_file(probe.path)
        for dep in probe.deps:
            collector.hook_import(probe.path, dep)

    def _register_probes(self, code: CodeType) -> None:
        """Recursively collect file-level probe constants from an instrumented code object.

        Probes live inside code object constants, so this walk is the only way to enumerate
        them for the re-arm registry. Runs once per newly instrumented code object.
        """
        for const in code.co_consts:
            if isinstance(const, _Probe):
                self._probes.append(const)
            elif isinstance(const, CodeType):
                self._register_probes(const)

    def arm(self, import_context: bool = False) -> None:
        if import_context:
            # Module bodies execute exactly once, so arming per module import would be pure
            # overhead: an import-coverage context has nothing that can re-fire.
            return
        for probe in self._probes:
            probe.dead = False

    def on_empty_module(self, code: CodeType, package: str) -> None:
        # The file-level selector injects a trap at the RESUME pseudo line start for empty
        # modules (see instrumentation_py3_10._valid_line_start_offsets), so empty modules
        # are recorded by their own instrumentation. Line-level mode also instruments them.
        return None


class LegacyBytecodeEngine(CoverageEngine):
    """Python 3.9: hand-rolled line-level bytecode instrumentation (no self-disabling traps)."""

    def instrument(self, code: CodeType, path: str, package: str) -> tuple[CodeType, CoverageLines]:
        return instrument_all_lines(code, self._collector.hook, path, package)


def create_engine(collector: "CoverageCollector") -> CoverageEngine:
    # is_at_least_py (vs raw sys.version_info comparisons) keeps the branches opaque to
    # mypy's version narrowing, so every engine stays reachable under the lint interpreter.
    if is_at_least_py(3, 12):
        return SysMonitoringEngine(collector)
    if is_at_least_py(3, 10):
        return BytecodeInjectionEngine(collector)
    return LegacyBytecodeEngine(collector)
