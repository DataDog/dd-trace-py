from argparse import ArgumentParser
from itertools import product
import logging
import os
from os.path import isabs
from os.path import relpath
import sys
from typing import Dict
from typing import List
from typing import Optional
from typing import TextIO
from typing import Tuple

from austin.stats import Frame  # type: ignore[import]
from austin.stats import InvalidSample
from austin.stats import Metrics
from austin.stats import Sample
from rich.console import Console  # type: ignore[import]
from rich.progress import track  # type: ignore[import]


FoldedStack = List[Frame]

CONSOLE = Console(file=sys.stderr)


if os.environ.get("DD_TRACE_DEBUG_ENABLE", False):
    logging.basicConfig(level=logging.DEBUG)
    LOGGER: Optional[logging.Logger] = logging.getLogger()
else:
    LOGGER = None


# -- MODEL --

CACHED_NORMALIZED_STACKS: Dict[int, FoldedStack] = {}


def _normalized_stack(stack: FoldedStack) -> FoldedStack:
    try:
        return CACHED_NORMALIZED_STACKS[id(stack)]
    except KeyError:
        CACHED_NORMALIZED_STACKS[id(stack)] = [_ for _ in stack if "ddtrace" not in _.filename]
    return CACHED_NORMALIZED_STACKS[id(stack)]


def score_stacks(
    x: List[Tuple[FoldedStack, Metrics]], y: List[Tuple[FoldedStack, Metrics]]
) -> List[Tuple[Tuple[int, int], float]]:
    """O(n * log(n)), n = len(x) * len(y)."""

    def score_stack(a: Tuple[FoldedStack, Metrics], b: Tuple[FoldedStack, Metrics]) -> float:
        """Score two folded stacks (modulo frames contributed by ddtrace).

        For multiple matches, prioritise those with similar metrics.
        """
        fa, ma = a
        fb, mb = b
        if _normalized_stack(fa) == _normalized_stack(fb):
            return 1 - abs(ma.time - mb.time) / (ma.time + mb.time)
        return 0

    return sorted(
        [
            ((i, j), score_stack(a, b))
            for ((i, a), (j, b)) in track(
                product(enumerate(x), enumerate(y)),
                total=len(x) * len(y),
                description="Scoring stacks",
                console=CONSOLE,
                transient=True,
            )
        ],
        key=lambda x: x[1],
        reverse=True,
    )


def match_folded_stacks(
    x: List[Tuple[FoldedStack, Metrics]],
    y: List[Tuple[FoldedStack, Metrics]],
    scale: float,
) -> List[Tuple[int, int]]:
    """O(len(x) * len(y))."""
    ss = score_stacks(x, y)
    mx, my = set(), set()
    matches = []
    for (i, j), s in ss:
        if i in mx or j in my or s == 0:
            continue
        mx.add(i)
        my.add(j)

        if LOGGER:
            stack = ":".join([_.function + ":" + str(_.line) for _ in _normalized_stack(x[i][0])])
            LOGGER.debug("Match: %f (%r | %r)  %s", s, x[i][1].time, y[j][1].time, stack)
            LOGGER.debug("")

        matches.append((i, j))

    matched_x = set()
    matched_y = set()
    matched_stacks_plus = []
    matched_stacks_minus = []
    new_stacks = []
    old_stacks = []

    # Matched stacks
    for i, j in matches:
        matched_x.add(i)
        matched_y.add(j)
        delta = (x[i][1].time - y[j][1].time) / scale

        if delta > 0:
            matched_stacks_plus.append((x[i][0], delta))
        elif delta < 0:
            matched_stacks_minus.append((y[j][0], -delta))

    # New stacks
    for i in range(len(x)):
        if i in matched_x:
            continue
        f, m = x[i]
        delta = m.time / scale

        if LOGGER:
            stack = ":".join([_.function + ":" + str(_.line) for _ in _normalized_stack(f)])
            LOGGER.debug("NO Match (%r)  %s", m.time, stack)
            LOGGER.debug("")

        new_stacks.append((f, delta))

    # Old stacks
    for j in range(len(y)):
        if j in matched_y:
            continue
        f, m = y[j]
        delta = m.time / scale

        if LOGGER:
            stack = ":".join([_.function + ":" + str(_.line) for _ in _normalized_stack(f)])
            LOGGER.debug("NO Match (%r)  %s", m.time, stack)
            LOGGER.debug("")

        old_stacks.append((f, delta))

    return matched_stacks_plus, matched_stacks_minus, new_stacks, old_stacks


def compressed(source: TextIO) -> Tuple[str, int]:
    """Compress the source."""
    stats: Dict[str, int] = {}
    total_time = 0
    for line in track(
        source.read().splitlines(keepends=False),
        description="Compressing",
        console=CONSOLE,
        transient=True,
    ):
        if line.startswith("# "):
            continue
        stack, _, metric = line.rpartition(" ")
        v = int(metric)
        stats[stack] = stats.setdefault(stack, 0) + v
        total_time += v

    return "\n".join([stack + " " + str(t) for stack, t in stats.items()]), total_time


def get_folded_stacks(text: str, threshold: float = 1e-3) -> List[Tuple[FoldedStack, Metrics]]:
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


def top(stacks):
    top_map = {}

    def _k(f):
        return "%s (%s:%d)" % (f.function, f.filename, f.line)

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

    return sorted(
        ((k, v) for k, v in top_map.items()),
        key=lambda e: e[1]["own"],
        reverse=True,
    )


# -- VIEW --


def make_top(stacks, overhead, output):
    output.write("Total overhead: %0.2f%%\n\n" % (overhead * 100))
    output.write("     %TOT   * %OWN  FUNCTION\n")
    output.write(
        "\n".join(
            [
                "{} {:6.2f}%  {:6.2f}%  {}".format("*" if "ddtrace" in f else " ", v["total"] * 100, v["own"] * 100, f)
                for f, v in top(stacks)
            ]
        )
    )


def make_stacks(matched_stacks_plus, matched_stacks_minus, new_stacks, old_stacks, stacks_stream):
    def repr_frame(frame: Frame) -> str:
        filename = frame.filename
        if isabs(filename):
            filename = relpath(filename)
        return "%s:%s:%d" % (filename, frame.function, frame.line)

    M = 1e8

    def join_stacks(stacks, prefix):
        return "\n".join(
            [
                ";".join(["P0", "T" + prefix] + [repr_frame(f) for f in fs]) + " %d" % int(delta * M)
                for fs, delta in stacks
            ]
        )

    stacks_stream.write(
        "\n".join(
            [
                join_stacks(s, p)
                for s, p in [
                    (matched_stacks_plus, "-MATCHED+"),
                    (new_stacks, "-NEW"),
                    (matched_stacks_minus, "-MATCHED-"),
                    (old_stacks, "-OLD"),
                ]
            ]
        )
    )


# -- CONTROLLER --


def diff(a: TextIO, b: TextIO, output_prefix: str, threshold: float = 1e-3) -> None:
    """Compare stacks and return a - b.

    The algorithm attempts to match stacks that look similar and returns
    only positive time differences (that is, stacks of b that took longer
    than those in a are not reported), plus any new stacks that are not in
    b.
    """
    ca, ta = compressed(a)
    cb, tb = compressed(b)
    overhead = (ta - tb) / tb

    fa = get_folded_stacks(ca, threshold)
    fb = get_folded_stacks(cb, threshold)
    matched_stacks_plus, matched_stacks_minus, new_stacks, old_stacks = match_folded_stacks(fa, fb, tb)

    with open(output_prefix + ".austin", "w") as stacks_stream:
        make_stacks(matched_stacks_plus, matched_stacks_minus, new_stacks, old_stacks, stacks_stream)
    with open(output_prefix + ".top", "w") as top_stream:
        make_top(matched_stacks_plus + new_stacks, overhead, top_stream)


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
        "output_prefix",
        type=str,
        help="The output file prefix",
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
        version="1.0.0",
    )

    args = argp.parse_args()

    with open(args.a) as a, open(args.b) as b:
        diff(a, b, args.output_prefix, args.threshold)


if __name__ == "__main__":
    main()
