# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "pyrometry",
# ]
# ///

r"""Import time analysis tool.

Typical usage: create a virtual environment (e.g. with riot), activate it,
and then run something like::

    python -X importtime -c "import ddtrace.auto" 2> imports.txt \\
      && uv run scripts/import-analysis/import_analysis.py view imports.txt > imports.html

To perform a comparison between two sets of samples (e.g. from a PR and base),
you first need to collect the data from two different versions (two different
commits). A standard way would be to run the loop::

    for i in {1..10}; do
        python -X importtime -c "import ddtrace.auto" 2> "import-pr-$i.txt"
    done

on the PR commit, and then::

    for i in {1..10}; do
        python -X importtime -c "import ddtrace.auto" 2> "import-base-$i.txt"
    done

on the base commit. Then you can run::

    uv run scripts/import-analysis/import_analysis.py compare > import_comparison.md
"""

import argparse
from collections import deque
from dataclasses import dataclass
from dataclasses import field
from math import floor
from math import log10
from pathlib import Path
import sys
import typing as t

from pyrometry.analysis import decompose_4way
from pyrometry.flamegraph import FlameGraph


microseconds = int

Z_THRESHOLD = 3.0  # 3 sigma rule
MIN_EFFECT_PCT = 5.0


@dataclass
class ImportStack:
    modules: tuple[str, ...]
    time: microseconds

    def __str__(self) -> str:
        return f"{';'.join(self.modules)} {self.time}"


@dataclass
class Import:
    module: str
    self: microseconds
    cumulative: microseconds
    level: int
    dependencies: list["Import"] = field(default_factory=list)

    @classmethod
    def parse(cls, line: str) -> "Import":
        assert line.startswith("import time:")

        data = line[len("import time:") :]
        self, cumulative, text = data.split(" | ", 2)
        n = len(text)
        name = text.lstrip()
        level = (n - len(name)) // 2

        return cls(name, int(self), int(cumulative), level)

    def flatten(self, tail=tuple()) -> t.Generator[ImportStack, None, None]:
        stack = (*tail, self.module)

        for dep in self.dependencies:
            yield from dep.flatten(stack)

        yield ImportStack(stack, self.self)

    @classmethod
    def expand(cls, stacks: list[ImportStack]) -> "Import":
        root = cls("root", 0, 0, -1)

        for stack in stacks:
            root.cumulative += stack.time
            current = root
            for module in stack.modules:
                try:
                    current = next(_ for _ in current.dependencies if _.module == module)
                    current.cumulative += stack.time
                except StopIteration:
                    current.dependencies.append(cls(module, 0, stack.time, current.level + 1))
                    current = current.dependencies[-1]
            current.self += stack.time

        return root

    def html(self, total: float) -> str:
        data = f"""<progress value="{self.cumulative}" max="{total}" style="width: 2em;"></progress>
            <code>{self.module}</code>
            <span style="color: #888; font-size:small; font-family: arial;">{self.cumulative / 1000:.3f} ms</span>
            <span style="color: #888; font-size:small; font-family: arial;">({self.cumulative / total:.2%})</span>
        """

        if self.dependencies:
            output = f"""<details style="padding-left: 1em"><summary>{data}</summary>"""

            for dep in sorted(self.dependencies, key=lambda dep: dep.cumulative, reverse=True):
                output += dep.html(total)

            output += "</details>\n"

        else:
            output = f"""<div style="padding-left: 2em">{data}</div>"""

        return output


class ImportFlameGraph(FlameGraph):
    @classmethod
    def loads(cls, text: str) -> "ImportFlameGraph":
        root = Import("root", 0, 0, -1)

        stack = deque([root])
        for line in text.splitlines()[::-1][:-1]:
            import_ = Import.parse(line)
            while stack[-1].level >= import_.level:
                stack.pop()

            stack[-1].dependencies.append(import_)
            stack.append(import_)

        (ddtrace,) = (_ for _ in root.dependencies if _.module == "ddtrace.auto")

        return super().loads("\n".join(str(s) for s in ddtrace.flatten()))

    @property
    def stacks(self) -> list[ImportStack]:
        return [ImportStack(modules=s.split(";"), time=v) for s, v in self.items()]


class Measure:
    def __init__(self, sample: list[float], unit: t.Optional[str] = None) -> None:
        self.size = n = len(sample)
        self.mean = m = sum(sample) / n
        self.std = ((sum(x**2 for x in sample) / n - m**2) * n / (n - 1)) ** 0.5
        self.unit = unit

    def __str__(self) -> str:
        d = floor(log10(self.std)) if self.std else 0
        n = round(self.mean, -d)
        e = round(self.std, -d)
        if d >= 0:
            n = int(n)
            e = int(e)
        u = f" {self.unit}" if self.unit is not None else ""
        return f"{n} ± {e}{u}"


def z_test(x: Measure, y: Measure) -> float:
    se = (x.std**2 / x.size + y.std**2 / y.size) ** 0.5
    if se == 0:
        if x.mean == y.mean:
            return 0.0
        return float("inf") if x.mean > y.mean else float("-inf")
    return (x.mean - y.mean) / se


def view(args: argparse.Namespace) -> None:
    fg = ImportFlameGraph.load(args.file)
    root = Import.expand(fg.stacks)
    total = root.cumulative

    print(f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Import Analysis</title></head><body>
<h1>Import time breakdown</h1>
<p>Total import time: {total / 1000:.3f} ms</p>
""")

    for dep in sorted(root.dependencies, key=lambda dep: dep.cumulative, reverse=True):
        print(dep.html(total))

    print("</body></html>")


def compare(args: argparse.Namespace) -> None:
    x = [ImportFlameGraph.load(f) for f in Path(args.dir).glob(f"{args.pr_prefix}*.txt")]
    y = [ImportFlameGraph.load(f) for f in Path(args.dir).glob(f"{args.base_prefix}*.txt")]

    if not x:
        print(f"No PR samples found matching '{args.pr_prefix}*.txt' in {args.dir}", file=sys.stderr)
        sys.exit(1)
    if not y:
        print(f"No base samples found matching '{args.base_prefix}*.txt' in {args.dir}", file=sys.stderr)
        sys.exit(1)

    # PR - base
    graphs: tuple[ImportFlameGraph, ImportFlameGraph, ImportFlameGraph, ImportFlameGraph] = decompose_4way(x, y)

    x_measure = Measure([fg.norm() / 1000 for fg in x], "ms")
    y_measure = Measure([fg.norm() / 1000 for fg in y], "ms")

    diff = x_measure.mean - y_measure.mean
    diff_std = (x_measure.std**2 / x_measure.size + y_measure.std**2 / y_measure.size) ** 0.5
    # hack
    diff_m = Measure([diff, diff], "ms")
    diff_m.std = diff_std

    # Assume large sample so that a z-test is appropriate.
    z = z_test(x_measure, y_measure)
    pct_change = (x_measure.mean - y_measure.mean) / y_measure.mean * 100 if y_measure.mean else 0.0
    is_regression = z > Z_THRESHOLD and pct_change > MIN_EFFECT_PCT

    print("## Bootstrap import analysis")
    print()
    print("Comparison of import times between this PR and base.")
    print()
    print("### Summary")
    print()
    print(f"The average import time from this PR is: {x_measure}.\n")
    print(f"The average import time from base is: {y_measure}.\n")
    print(f"The import time difference between this PR and base is: {diff_m}.\n")
    print(f"z = {z:.2f}, change = {pct_change:+.1f}%\n")
    if not is_regression:
        print("The difference is **not** a significant regression.\n")
    print()
    print("### Import time breakdown")
    print()

    if any(graphs):
        for name, graph in zip(["appeared", "disappeared", "grown", "shrunk"], graphs):
            if graph:
                root = Import.expand(graph.stacks)
                print(f"The following import paths have **{name}**:")
                for dep in sorted(root.dependencies, key=lambda dep: dep.cumulative, reverse=True):
                    print(dep.html(x_measure.mean * 1000))
                print()
    else:
        print("No import paths have changed significantly.")

    if is_regression:
        msg = f"Import time has increased significantly (z = {z:.2f}, {pct_change:+.1f}%)."
        raise ValueError(msg)


def main() -> None:
    parser = argparse.ArgumentParser(description="Import time analysis tool")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # view subcommand
    view_parser = subparsers.add_parser("view", help="Generate HTML from a single import data file")
    view_parser.add_argument("file", type=Path, help="Path to an import data file")
    view_parser.set_defaults(func=view)

    # compare subcommand
    compare_parser = subparsers.add_parser(
        "compare",
        help="Compare import times between PR and base samples",
    )
    compare_parser.add_argument(
        "--dir",
        type=Path,
        default=Path.cwd(),
        help="Directory containing sample files (default: cwd)",
    )
    compare_parser.add_argument(
        "--pr-prefix",
        default="import-pr-",
        help="Filename prefix for PR samples (default: import-pr-)",
    )
    compare_parser.add_argument(
        "--base-prefix",
        default="import-base-",
        help="Filename prefix for base samples (default: import-base-)",
    )
    compare_parser.set_defaults(func=compare)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    try:
        main()
    except ValueError as e:
        print(e, file=sys.stderr)
        sys.exit(1)
