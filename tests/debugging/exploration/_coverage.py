from collections import defaultdict
import os
import sys

from debugger import COLS
from debugger import CWD
from debugger import ExplorationDebugger
from debugger import ModuleCollector
from debugger import status

from ddtrace.debugging._probe.model import LineProbe
from ddtrace.internal.module import origin
from ddtrace.internal.utils.formats import asbool


_tracked_modules = {}

ENABLED = asbool(os.getenv("DD_DEBUGGER_EXPL_COVERAGE_ENABLED", True))
DELETE_LINE_PROBE = asbool(os.getenv("DD_DEBUGGER_EXPL_DELETE_LINE_PROBE", False))


class LineCollector(ModuleCollector):
    def on_collect(self, discovery):
        o = origin(discovery._module)
        status("Collecting lines from %s" % o)
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
        seen_lines_map = defaultdict(set)
        for probe in (_ for _ in cls.get_triggered_probes() if isinstance(_, LineProbe)):
            seen_lines_map[probe.source_file].add(probe.line)

        print(("{:=^%ds}" % COLS).format(" Line coverage "))
        print()
        head = "{:<40} {:>5} {:>6}".format("Source", "Lines", "Covered")
        print(head)
        print("=" * len(head))
        total_lines = 0
        total_covered = 0
        for o, (_, lines) in sorted(_tracked_modules.items(), key=lambda x: x[0]):
            total_lines += len(lines)
            seen_lines = seen_lines_map[o]
            total_covered += len(seen_lines)
            print(
                "{:<40} {:>5} {: 6.0f}%".format(
                    os.path.relpath(o, CWD),
                    len(lines),
                    len(seen_lines) * 100.0 / len(lines) if lines else 0,
                )
            )
        if not total_lines:
            print("No lines found")
            return
        print("-" * len(head))
        print("{:<40} {:>5} {: 6.0f}%".format("TOTAL", total_lines, total_covered * 100.0 / total_lines))
        print()

    @classmethod
    def on_disable(cls):
        cls.report_coverage()

    @classmethod
    def on_snapshot(cls, snapshot):
        if DELETE_LINE_PROBE:
            cls.delete_probe(snapshot.probe)


if ENABLED:
    LineCoverage.enable()
