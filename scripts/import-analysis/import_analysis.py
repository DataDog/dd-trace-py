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
    return (x.mean - y.mean) / ((x.std**2 / x.size + y.std**2 / y.size) ** 0.5)


def main() -> None:
    x = [ImportFlameGraph.load(f) for f in Path.cwd().glob("import-pr-*.txt")]
    y = [ImportFlameGraph.load(f) for f in Path.cwd().glob("import-base-*.txt")]

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

    print("## Bootstrap import analysis")
    print()
    print("Comparison of import times between this PR and base.")
    print()
    print("### Summary")
    print()
    print(f"The average import time from this PR is: {str(x_measure)}.\n")
    print(f"The average import time from base is: {str(y_measure)}.\n")
    print(f"The import time difference between this PR and base is: {str(diff_m)}.\n")
    if abs(z) <= Z_THRESHOLD:
        print(f"The difference is not statistically significant (z = {z:.2f}).\n")
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

    if z > Z_THRESHOLD:
        msg = f"Import time has increased significantly (z = {z:.2f})."
        raise ValueError(msg)


if __name__ == "__main__":
    try:
        main()
    except ValueError as e:
        print(e, file=sys.stderr)
        sys.exit(1)
