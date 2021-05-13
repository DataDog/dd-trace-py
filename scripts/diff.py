import sys
from argparse import ArgumentParser
from io import StringIO
from itertools import product
from typing import List, TextIO, Tuple

from austin.stats import (
    ZERO,
    AustinStats,
    Frame,
    InvalidSample,
    Metrics,
    Sample,
)
from rich.console import Console
from rich.progress import track

FoldedStack = List[Frame]
CONSOLE = Console(file=sys.stderr)


def _similarities(x: List[FoldedStack], y: List[FoldedStack]) -> List[Tuple[Tuple[int, int], float]]:
    """O(n * log(n)), n = len(x) * len(y)."""

    def score(a: List[Frame], b: List[Frame]) -> float:
        """Score two folded stacks (modulo frames contributed by ddtrace).

        For multiple matches, prioritise those with similar metrics.
        """
        fa, ma = a
        fb, mb = b
        if [_ for _ in fa if "ddtrace" not in _.filename] == [_ for _ in fb if "ddtrace" not in _.filename]:
            return 1 - abs(ma.time - mb.time) / (ma.time + mb.time)
        return 0

    return sorted(
        [
            ((i, j), score(a, b))
            for ((i, a), (j, b)) in track(
                product(enumerate(x), enumerate(y)),
                total=len(x) * len(y),
                description="Scoring matches",
                console=CONSOLE,
                transient=True,
            )
        ],
        key=lambda x: x[1],
        reverse=True,
    )


def _match(x: List[FoldedStack], y: List[FoldedStack], threshold: float = 0.5) -> List[Tuple[int, int]]:
    """O(len(x) * len(y))."""
    ss = _similarities(x, y)
    mx, my = set(), set()
    matches = set()
    for (i, j), s in ss:
        if i in mx or j in my or s <= threshold:
            continue
        mx.add(i)
        my.add(j)
        matches.add((i, j))
    return matches


def diff(a: TextIO, b: TextIO, threshold: float = 1e-3, top: bool = False) -> str:
    """Compare stacks and return a - b.

    The algorithm attempts to match stacks that look similar and returns
    only positive time differences (that is, stacks of b that took longer
    than those in a are not reported), plus any new stacks that are not in
    b.

    The return value is a string with collapsed stacks followed by the delta
    of the metrics on each line.
    """

    def compressed(source: TextIO) -> str:
        """Compress the source."""
        stats = AustinStats()
        for line in track(
            source.read().splitlines(keepends=False),
            description="Compressing",
            console=CONSOLE,
            transient=True,
        ):
            stats.update(Sample.parse(line))

        buffer = StringIO()
        stats.dump(buffer)

        total_time = sum(t.total.time for p in stats.processes.values() for t in p.threads.values())
        return buffer.getvalue(), total_time

    def get_frames(text: str, threshold: float = 1e-3) -> List[Tuple[FoldedStack, Metrics]]:
        """Get the folded stacks and metrics from a string of samples."""
        x = []
        max_time = 1
        for _ in track(
            text.splitlines(keepends=False),
            description="Extracting frames",
            console=CONSOLE,
            transient=True,
        ):
            try:
                sample = Sample.parse(_)
                if sample.metrics.time > max_time:
                    max_time = sample.metrics.time
                x.append((sample.frames, sample.metrics))
            except InvalidSample:
                continue
        return [_ for _ in x if _[1].time / max_time > threshold]

    ca, ta = compressed(a)
    cb, tb = compressed(b)
    fa = get_frames(ca, threshold)
    fb = get_frames(cb, threshold)
    ms = _match(fa, fb)

    matched = set()
    stacks = []

    # Matched stacks
    for i, j in ms:
        matched.add(i)
        delta = (fa[i][1].time - fb[j][1].time) / tb

        if delta > 0:
            stacks.append((fa[i][0], delta))

    # New stacks
    for i in [_ for _ in range(len(fa)) if _ not in matched]:
        f, m = fa[i]
        delta = m.time / tb
        stacks.append((f, delta))

    if top:
        top_map = {}

        def _k(f):
            return "%s (%s)" % (
                "%s:%d" % (f.function, f.line) if f.function[0] == "<" else f.function,
                f.filename,
            )

        for fs, delta in stacks:
            seen_fs = set()
            for f in fs:
                key = _k(f)
                if key in seen_fs:
                    continue
                top_map.setdefault(key, {"own": 0, "total": 0})["total"] += delta
                seen_fs.add(key)
            if fs:
                top_map[key]["own"] += delta

        return "\n".join(
            [
                "{:6.2f}%  {:6.2f}%  {}".format(v["total"] * 100, v["own"] * 100, f)
                for f, v in sorted(
                    ((k, v) for k, v in top_map.items()),
                    key=lambda e: e[1]["own"],
                    reverse=True,
                )[:25]
            ]
        )

    return "\n".join([";".join(["P0", "T0"] + [str(_) for _ in f]) + " %d" % int(delta * 1e8) for f, delta in stacks])


def main() -> None:
    """Diff tool for Austin samples."""
    argp = ArgumentParser(
        prog="austin-diff",
        description=("Compute the diff between two austin frame stack sample files"),
    )

    argp.add_argument(
        "a",
        type=str,
        help="The minuend collapsed stacks",
    )
    argp.add_argument(
        "b",
        type=str,
        help="The subtrahend collapsed stacks",
    )
    argp.add_argument(
        "-o",
        "--output",
        type=str,
        help="The output file",
    )
    argp.add_argument(
        "-T",
        "--top",
        action="store_true",
        default=False,
        help="Give a top-like result instead of folded stacks",
    )
    argp.add_argument(
        "-t",
        "--threshold",
        type=float,
        default=1e-3,
        help="Relative stack width threshold. Increase for speedups",
    )
    argp.add_argument(
        "-V",
        "--version",
        action="version",
        version="0.1.0",
    )

    args = argp.parse_args()

    with open(args.a) as a, open(args.b) as b:
        result = diff(a, b, threshold=args.threshold, top=args.top)
        if args.output is not None:
            with open(args.output, "w") as fout:
                fout.write(result)
        else:
            print(result)


if __name__ == "__main__":
    main()
