"""Generate LCOV reports without retaining every file's parsed source and analysis."""

from io import StringIO
import tempfile
from typing import IO
from typing import Any
from typing import Optional
from typing import cast


try:
    from coverage import Coverage
    from coverage.control import override_config
    from coverage.lcovreport import LcovReporter
    from coverage.report_core import get_analysis_to_report
    from coverage.report_core import render_report
except ImportError:
    _LCOV_AVAILABLE = False
else:
    _LCOV_AVAILABLE = True

    class _StreamingLcovReporter(LcovReporter):
        def report(self, morfs: Any, outfile: IO[str]) -> float:
            self.coverage.get_data().set_query_contexts(self.coverage.config.report_contexts)
            # coverage.py sorts all file analyses, retaining their ASTs until the report
            # is written. Spool rendered records instead so only one parsed file and
            # record are needed at a time; sort lightweight offsets into the spool.
            records: list[tuple[str, int, int]] = []
            with tempfile.TemporaryFile(mode="w+", encoding="utf-8", newline="") as spool:
                for file_reporter, analysis in get_analysis_to_report(self.coverage, morfs):
                    filename = file_reporter.relative_filename()
                    self.total += analysis.numbers
                    with StringIO() as buffer:
                        # Older coverage.py versions use get_lcov instead, and are
                        # handled by the compatibility path in report_lcov.
                        getattr(self, "lcov_file")(filename, file_reporter, analysis, buffer)
                        record = buffer.getvalue()
                    records.append((filename, spool.tell(), len(record)))
                    spool.write(record)
                    del file_reporter, analysis, record

                for _, offset, length in sorted(records):
                    spool.seek(offset)
                    # Store character counts alongside text-stream seek cookies so
                    # non-ASCII paths and source text also copy exactly.
                    for position in range(0, length, 65536):
                        outfile.write(spool.read(min(65536, length - position)))

            return self.total.n_statements and self.total.pc_covered


def report_lcov(cov: Any, **kwargs: Any) -> Optional[float]:
    """Use coverage.py's file renderer and report configuration with bounded analysis memory."""
    if (
        not _LCOV_AVAILABLE
        or not isinstance(cov, Coverage)
        or not hasattr(LcovReporter, "lcov_file")
        or kwargs.keys() - {"morfs", "outfile", "ignore_errors", "omit", "include", "contexts"}
    ):
        return cast(Optional[float], cov.lcov_report(**kwargs))

    cov._prepare_data_for_reporting()
    with override_config(
        cov,
        ignore_errors=kwargs.get("ignore_errors"),
        report_omit=kwargs.get("omit"),
        report_include=kwargs.get("include"),
        lcov_output=kwargs.get("outfile"),
        report_contexts=kwargs.get("contexts"),
    ):
        return render_report(cov.config.lcov_output, _StreamingLcovReporter(cov), kwargs.get("morfs"), cov._message)
