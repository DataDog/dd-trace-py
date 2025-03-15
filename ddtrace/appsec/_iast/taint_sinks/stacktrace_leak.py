import os
import re

from ddtrace.appsec._iast._logs import iast_error

from ..._constants import IAST_SPAN_TAGS
from .. import oce
from .._iast_request_context import set_iast_stacktrace_reported
from .._metrics import _set_metric_iast_executed_sink
from .._metrics import increment_iast_span_metric
from ..constants import HTML_TAGS_REMOVE
from ..constants import STACKTRACE_EXCEPTION_REGEX
from ..constants import STACKTRACE_FILE_LINE
from ..constants import VULN_STACKTRACE_LEAK
from ..taint_sinks._base import VulnerabilityBase


@oce.register
class StacktraceLeak(VulnerabilityBase):
    vulnerability_type = VULN_STACKTRACE_LEAK
    skip_location = True


def asm_report_stacktrace_leak_from_django_debug_page(exc_name, module):
    increment_iast_span_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SINK, StacktraceLeak.vulnerability_type)
    _set_metric_iast_executed_sink(StacktraceLeak.vulnerability_type)
    evidence = "Module: %s\nException: %s" % (module, exc_name)
    StacktraceLeak.report(evidence_value=evidence)
    set_iast_stacktrace_reported(True)


def asm_check_stacktrace_leak(content: str) -> None:
    if not content:
        return

    try:
        # Quick check to avoid the slower operations if on stacktrace
        if "Traceback (most recent call last):" not in content:
            return

        text = HTML_TAGS_REMOVE.sub("", content)
        lines = [line.strip() for line in text.splitlines() if line.strip()]

        file_lines = []
        exception_line = ""

        for i, line in enumerate(lines):
            if line.startswith("Traceback (most recent call last):"):
                # from here until we find an exception line
                continue

            # See if this line is a "File ..." line
            m_file = STACKTRACE_FILE_LINE.match(line)
            if m_file:
                file_lines.append(m_file.groups())
                continue

            # See if this line might be the exception line
            m_exc = STACKTRACE_EXCEPTION_REGEX.match(line)
            if m_exc:
                # We consider it as the "final" exception line. Keep it.
                exception_line = m_exc.group("exc")
                # We won't break immediately because sometimes Django
                # HTML stack traces can have repeated exception lines, etc.
                # But typically the last match is the real final exception
                # We'll keep updating exception_line if we see multiple
                continue

        if not file_lines and not exception_line:
            return

        module_path = None
        if file_lines:
            # file_lines looks like [ ("/path/to/file.py", "line_no", "funcname"), ... ]
            last_file_entry = file_lines[-1]
            module_path = last_file_entry[0]  # the path in quotes

        # Attempt to convert a path like "/myproj/foo/bar.py" into "foo.bar"
        # or "myproj.foo.bar" depending on your directory structure.
        # This is a *best effort* approach (it can be environment-specific).
        module_name = ""
        if module_path:
            mod_no_ext = re.sub(r"\.py$", "", module_path)
            parts: list[str] = []
            while True:
                head, tail = os.path.split(mod_no_ext)
                if tail:
                    parts.insert(0, tail)
                    mod_no_ext = head
                else:
                    # might still have a leftover 'head' if it’s not just root
                    break

            module_name = ".".join(parts)
            if not module_name:
                module_name = module_path  # fallback: just the path

        increment_iast_span_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SINK, StacktraceLeak.vulnerability_type)
        _set_metric_iast_executed_sink(StacktraceLeak.vulnerability_type)
        evidence = "Module: %s\nException: %s" % (module_name.strip(), exception_line.strip())
        StacktraceLeak.report(evidence_value=evidence)
    except Exception as e:
        iast_error(f"propagation::sink_point::Error in check stacktrace leak. {e}")
