import contextlib
from itertools import count
from itertools import groupby
import json
import os
import sys
from typing import Iterable
from typing import List
from typing import Tuple

from coverage import Coverage
from coverage.numbits import numbits_to_nums


@contextlib.contextmanager
def cover(span, root=None, **kwargs):
    coverage_kwargs = {
        "data_file": None,
        "source": [root] if root else None,
        "config_file": False,
    }
    coverage_kwargs.update(kwargs)
    cov = Coverage(**coverage_kwargs)
    cov.start()
    cov.switch_context(str(span.trace_id))
    yield cov
    cov.stop()
    span.set_tag("test.coverage", CIVisibilityReporter(cov).build(test_id=str(span.trace_id), root=root))


def segments(lines):
    """Extract the relevant report data for a single file."""

    def as_segments(it):
        # type: (Iterable[int]) -> Tuple[int, int, int, int, int]
        sequence = list(it)  # type: List[int]
        return (sequence[0], 0, sequence[-1], 0, -1)

    executed = sorted(lines)
    return [as_segments(g) for _, g in groupby(executed, lambda n, c=count(): n - next(c))]


class CIVisibilityReporter:
    """A reporter for writing CI Visibility JSON coverage results."""

    report_type = "CI Visibility JSON report"

    def __init__(self, coverage):
        self.coverage = coverage
        self.config = self.coverage.config

    def _lines(self, context):
        data = self.coverage.get_data()
        context_id = data._context_id(context)
        data._start_using()
        with data._connect() as con:
            query = (
                "select file.path, line_bits.numbits "
                "from line_bits "
                "join file on line_bits.file_id = file.id "
                "where context_id = ?"
            )
            data = [context_id]
            bitmaps = list(con.execute(query, data))
            return {row[0]: numbits_to_nums(row[1]) for row in bitmaps if not row[0].startswith("..")}

    def build(self, test_id=None, root=None):
        """Generate a CI Visibility structure."""
        return [
            {
                "filename": os.path.relpath(filename, root) if root is not None else filename,
                "segments": segments(lines),
            }
            for filename, lines in self._lines(test_id).items()
        ]

    def report(self, outfile=None, test_id=None, root=None):
        """Generate a CI Visibility report.

        `outfile` is a file object to write the json to.
        """
        outfile = outfile or sys.stdout

        json.dump(
            {
                "type": "coverage",
                "version": 1,
                "content": {
                    "test_id": test_id,
                    "files": self.build(test_id=test_id, root=root),
                },
            },
            outfile,
            indent=(4 if self.config.json_pretty_print else None),
        )
