from ddtrace.appsec._iast._iast_request_context import get_iast_reporter


def _get_span_report():
    span_report = get_iast_reporter()
    return span_report


def _get_iast_data():
    span_report = _get_span_report()
    data = span_report.build_and_scrub_value_parts()
    return data
