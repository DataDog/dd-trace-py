from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Text

from ddtrace.appsec._constants import IAST_SPAN_TAGS
from ddtrace.appsec._iast import oce
from ddtrace.appsec._iast._logs import iast_error
from ddtrace.appsec._iast._metrics import _set_metric_iast_executed_sink
from ddtrace.appsec._iast._metrics import increment_iast_span_metric
from ddtrace.appsec._iast._taint_utils import check_tainted_dbapi_args
from ddtrace.appsec._iast.constants import VULN_SQL_INJECTION
from ddtrace.appsec._iast.taint_sinks._base import VulnerabilityBase
from ddtrace.settings.asm import config as asm_config


@oce.register
class SqlInjection(VulnerabilityBase):
    vulnerability_type = VULN_SQL_INJECTION


def check_and_report_sqli(
    args: List[Any], kwargs: Dict[str, Any], integration_name: Text, method: Callable[..., Any]
) -> bool:
    """Check for SQL injection vulnerabilities in database operations and report them.

    This function analyzes database operation arguments for potential SQL injection
    vulnerabilities. It checks if the operation is from a supported DBAPI integration,
    if the method is 'execute', and if the first argument contains tainted data that
    hasn't been marked as secure.

    Note:
        This function is part of the IAST (Interactive Application Security Testing)
        system and is used to detect potential SQL injection vulnerabilities at runtime.
    """
    reported = False
    try:
        if asm_config.is_iast_request_enabled:
            if SqlInjection.has_quota() and check_tainted_dbapi_args(args, kwargs, integration_name, method):
                SqlInjection.report(evidence_value=args[0], dialect=integration_name)

            # Reports Span Metrics
            increment_iast_span_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SINK, SqlInjection.vulnerability_type)
            # Report Telemetry Metrics
            _set_metric_iast_executed_sink(SqlInjection.vulnerability_type)
    except Exception as e:
        iast_error(f"propagation::sink_point::Error in check_and_report_sqli. {e}")
    return reported
