from collections.abc import Mapping
from typing import Any

from ddtrace.appsec._asm_request_context import call_waf_callback
from ddtrace.appsec._asm_request_context import get_blocked
from ddtrace.appsec._asm_request_context import in_asm_context
from ddtrace.appsec._constants import EXPLOIT_PREVENTION
from ddtrace.appsec._metrics import report_rasp_skipped
from ddtrace.appsec._rasp import get_rasp_capability
from ddtrace.appsec._rasp import must_block
from ddtrace.internal import core
from ddtrace.internal._exceptions import BlockingException


_DIALECTS = {
    "mariadb": "mariadb",
    "mysql": "mysql",
    "postgres": "postgresql",
    "pymysql": "mysql",
    "pyodbc": "odbc",
    "sql": "sql",
    "sqlite": "sqlite",
    "vertica": "vertica",
}


def patch() -> None:
    core.on("asm.block.dbapi.execute", on_execute)


def unpatch() -> None:
    core.reset_listeners("asm.block.dbapi.execute", on_execute)


def on_execute(instrument_self: object, query: object, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
    if not (get_rasp_capability("sqli") and isinstance(query, str) and query):
        return
    config = getattr(instrument_self, "_self_config", {})
    span_name_prefix = config.get("_dbapi_span_name_prefix", "") if isinstance(config, Mapping) else ""
    dialect = _DIALECTS.get(span_name_prefix, "") if isinstance(span_name_prefix, str) else ""
    if in_asm_context():
        result = call_waf_callback(
            {EXPLOIT_PREVENTION.ADDRESS.SQLI: query, EXPLOIT_PREVENTION.ADDRESS.SQLI_TYPE: dialect},
            crop_trace="execute_4C9BAC8E228EB347",
            rule_type=EXPLOIT_PREVENTION.TYPE.SQLI,
        )
        if result and must_block(result.actions):
            raise BlockingException(get_blocked(), EXPLOIT_PREVENTION.BLOCKING, EXPLOIT_PREVENTION.TYPE.SQLI, query)
    else:
        report_rasp_skipped(EXPLOIT_PREVENTION.TYPE.SQLI, False)
