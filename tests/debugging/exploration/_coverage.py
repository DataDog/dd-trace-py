from collections import defaultdict
from pathlib import Path
import sys
from types import ModuleType
import typing as t

from _config import config as expl_config
from debugger import ExplorationDebugger
from debugger import ModuleCollector
from debugger import config
from debugger import status
from debugging.utils import create_snapshot_line_probe
from output import log
from utils import COLS
from utils import CWD

from ddtrace.debugging._function.discovery import FunctionDiscovery
from ddtrace.debugging._probe.model import LogLineProbe
from ddtrace.debugging._signal.model import SignalState
from ddtrace.debugging._signal.snapshot import Snapshot
from ddtrace.internal.module import origin


# Track all the covered modules and its lines. Indexed by module origin.
_tracked_modules: t.Dict[str, t.Tuple[ModuleType, t.Set[int]]] = {}


class LineCollector(ModuleCollector):
    def on_collect(self, discovery: FunctionDiscovery) -> None:
        o = origin(discovery._module)
        status("[coverage] collecting lines from %s" % o)
        _tracked_modules[o] = (discovery._module, {_ for _ in discovery.keys()})
        LineCoverage.add_probes(
            [
                create_snapshot_line_probe(
                    probe_id="@".join([str(hash(f)), str(line)]),
                    source_file=origin(sys.modules[f.__module__]),
                    line=line,
                    rate=0.0,
                    limits=expl_config.limits,
                )
                for line, functions in discovery.items()
                for f in functions
            ]
        )


class LineCoverage(ExplorationDebugger):
    __watchdog__ = LineCollector

    @classmethod
    def report_coverage(cls) -> None:
        seen_lines_map: t.Dict[Path, set] = defaultdict(set)
        for probe in (_ for _ in cls.get_triggered_probes() if isinstance(_, LogLineProbe)):
            seen_lines_map[t.cast(LogLineProbe, probe).resolved_source_file].add(probe.line)

        try:
            w = max(len(str(o.relative_to(CWD))) for o in _tracked_modules)
        except ValueError:
            w = int(COLS * 0.75)
        log(("{:=^%ds}" % COLS).format(" Line coverage "))
        log("")
        head = ("{:<%d} {:>5} {:>6}" % w).format("Source", "Lines", "Covered")
        log(head)
        log("=" * len(head))

        total_lines = 0
        total_covered = 0
        for o, (_, lines) in sorted(_tracked_modules.items(), key=lambda x: x[0]):
            total_lines += len(lines)
            seen_lines = seen_lines_map[o]
            total_covered += len(seen_lines)
            log(
                ("{:<%d} {:>5} {: 6.0f}%%" % w).format(
                    str(o.relative_to(CWD)),
                    len(lines),
                    len(seen_lines) * 100.0 / len(lines) if lines else 0,
                )
            )
        if not total_lines:
            log("No lines found")
            return
        log("-" * len(head))
        log(("{:<%d} {:>5} {: 6.0f}%%" % w).format("TOTAL", total_lines, total_covered * 100.0 / total_lines))
        log("")

    @classmethod
    def on_disable(cls) -> None:
        cls.report_coverage()

    @classmethod
    def on_snapshot(cls, snapshot: Snapshot) -> None:
        if config.coverage.delete_probes:
            # Change the state of the snapshot to avoid setting the emitting
            # state. This would be too late, when the probe is already
            # deleted from the registry.
            snapshot.state = SignalState.NONE
            cls.delete_probe(snapshot.probe)


if config.coverage.enabled:
    LineCoverage.enable()
