from collections import defaultdict
import os
import sys
from types import ModuleType
import typing as t

from debugger import COLS
from debugger import CWD
from debugger import ExplorationDebugger
from debugger import ModuleCollector
from debugger import status

from ddtrace.debugging._function.discovery import FunctionDiscovery
from ddtrace.debugging._probe.model import LineProbe
from ddtrace.debugging._snapshot.model import Snapshot
from ddtrace.internal.module import origin
from ddtrace.internal.utils.formats import asbool


# Track all the covered modules and its lines. Indexed by module origin.
_tracked_modules = {}  # type: t.Dict[str, t.Tuple[ModuleType, t.Set[int]]]

ENABLED = asbool(os.getenv("DD_DEBUGGER_EXPL_COVERAGE_ENABLED", True))
DELETE_LINE_PROBE = asbool(os.getenv("DD_DEBUGGER_EXPL_DELETE_LINE_PROBE", False))


class LineCollector(ModuleCollector):
    def on_collect(self, discovery):
        # type: (FunctionDiscovery) -> None
        o = origin(discovery._module)
        status("[coverage] collecting lines from %s" % o)
        _tracked_modules[o] = (discovery._module, {_ for _ in discovery.keys()})
        LineCoverage.add_probes(
            [
                LineProbe(
                    probe_id="@".join([str(hash(f)), str(line)]),
                    source_file=origin(sys.modules[f.__module__]),
                    line=line,
                    rate=0.0,
                )
                for line, functions in discovery.items()
                for f in functions
            ]
        )


class LineCoverage(ExplorationDebugger):
    __watchdog__ = LineCollector

    @classmethod
    def report_coverage(cls):
        # type: () -> None
        seen_lines_map = defaultdict(set)
        for probe in (_ for _ in cls.get_triggered_probes() if isinstance(_, LineProbe)):
            seen_lines_map[probe.source_file].add(probe.line)

        try:
            w = max(len(os.path.relpath(o, CWD)) for o in _tracked_modules)
        except ValueError:
            w = int(COLS * 0.75)
        print(("{:=^%ds}" % COLS).format(" Line coverage "))
        print("")
        head = ("{:<%d} {:>5} {:>6}" % w).format("Source", "Lines", "Covered")
        print(head)
        print("=" * len(head))

        total_lines = 0
        total_covered = 0
        for o, (_, lines) in sorted(_tracked_modules.items(), key=lambda x: x[0]):
            total_lines += len(lines)
            seen_lines = seen_lines_map[o]
            total_covered += len(seen_lines)
            print(
                ("{:<%d} {:>5} {: 6.0f}%%" % w).format(
                    os.path.relpath(o, CWD),
                    len(lines),
                    len(seen_lines) * 100.0 / len(lines) if lines else 0,
                )
            )
        if not total_lines:
            print("No lines found")
            return
        print("-" * len(head))
        print(("{:<%d} {:>5} {: 6.0f}%%" % w).format("TOTAL", total_lines, total_covered * 100.0 / total_lines))
        print("")

    @classmethod
    def on_disable(cls):
        # type: () -> None
        cls.report_coverage()

    @classmethod
    def on_snapshot(cls, snapshot):
        # type: (Snapshot) -> None
        if DELETE_LINE_PROBE:
            cls.delete_probe(snapshot.probe)


if ENABLED:
    LineCoverage.enable()
