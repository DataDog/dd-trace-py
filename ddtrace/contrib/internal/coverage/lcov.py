"""Generate LCOV reports without retaining every file's parsed source and analysis."""

from inspect import signature
from io import StringIO
import tempfile
from typing import IO
from typing import Any
from typing import Iterator
from typing import Optional
from typing import cast


try:
    from coverage import Coverage
    from coverage.control import override_config
    from coverage.exceptions import NoDataError
    from coverage.exceptions import NotPython
    from coverage.files import GlobMatcher
    from coverage.files import prep_patterns
    from coverage.lcovreport import LcovReporter
    from coverage.report_core import get_analysis_to_report
    from coverage.report_core import render_report
except ImportError:
    _LCOV_AVAILABLE = False
else:
    _LCOV_AVAILABLE = True

    def _get_legacy_analysis_to_report(cov: Any, morfs: Any) -> Iterator[tuple[Any, Any]]:
        # Older coverage.py iterators keep every FileReporter alive, including
        # its parsed source. Match their selection and error handling, but pop
        # completed reporters so memory is bounded on these versions too.
        reporters = [entry if isinstance(entry, tuple) else (entry, entry) for entry in cov._get_file_reporters(morfs)]
        config = cov.config
        if config.report_include:
            matcher = GlobMatcher(prep_patterns(config.report_include), "report_include")
            reporters = [(fr, morf) for fr, morf in reporters if matcher.match(fr.filename)]
        if config.report_omit:
            matcher = GlobMatcher(prep_patterns(config.report_omit), "report_omit")
            reporters = [(fr, morf) for fr, morf in reporters if not matcher.match(fr.filename)]
        if not reporters:
            raise NoDataError("No data to report.")

        reporters.sort(reverse=True)
        while reporters:
            fr, morf = reporters.pop()
            try:
                analysis = cov._analyze(morf)
            except NotPython:
                if fr.should_be_python():
                    if config.ignore_errors:
                        cov._warn(f"Couldn't parse Python file '{fr.filename}'", slug="couldnt-parse")
                    else:
                        raise
            except Exception as exc:
                if config.ignore_errors:
                    cov._warn(f"Couldn't parse '{fr.filename}': {exc}".rstrip(), slug="couldnt-parse")
                else:
                    raise
            else:
                yield fr, analysis
                del analysis
            del fr, morf

    class _StreamingLcovReporter(LcovReporter):
        def report(self, morfs: Any, outfile: IO[str]) -> float:
            self.coverage.get_data().set_query_contexts(self.coverage.config.report_contexts)
            # coverage.py sorts all file analyses, retaining their ASTs until the report
            # is written. Spool rendered records instead so only one parsed file and
            # record are needed at a time; sort lightweight offsets into the spool.
            records: list[tuple[str, int, int]] = []
            analyze = (
                get_analysis_to_report
                if "file_reporter" in signature(Coverage._analyze).parameters
                else _get_legacy_analysis_to_report
            )
            modern_renderer = hasattr(self, "lcov_file")
            with tempfile.TemporaryFile(mode="w+", encoding="utf-8", newline="") as spool:
                for file_reporter, analysis in analyze(self.coverage, morfs):
                    filename = file_reporter.relative_filename()
                    with StringIO() as buffer:
                        if modern_renderer:
                            self.total += analysis.numbers
                            getattr(self, "lcov_file")(filename, file_reporter, analysis, buffer)
                        else:
                            # get_lcov updates totals and preserves absolute-path
                            # ordering, unlike the newer LCOV renderer.
                            getattr(self, "get_lcov")(file_reporter, analysis, buffer)
                            filename = file_reporter.filename
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
        or not (hasattr(LcovReporter, "lcov_file") or hasattr(LcovReporter, "get_lcov"))
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
