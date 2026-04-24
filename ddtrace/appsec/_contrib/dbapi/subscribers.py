from typing import Union

from ddtrace.appsec._asm_request_context import call_waf_callback
from ddtrace.appsec._asm_request_context import get_blocked
from ddtrace.appsec._asm_request_context import in_asm_context
from ddtrace.appsec._constants import EXPLOIT_PREVENTION
from ddtrace.appsec._metrics import report_rasp_skipped
from ddtrace.appsec._processor import AppSecSpanProcessor
from ddtrace.contrib._events.database import DbApiExecuteEvent
from ddtrace.internal._exceptions import BlockingException
from ddtrace.internal.core.subscriber import Subscriber


DIALECT_FROM_SPAN: dict[Union[str, None], str] = {
    "mariadb": "mariadb",
    "mysql": "mysql",
    "postgres": "postgresql",
    "pymysql": "mysql",
    "pyodbc": "odbc",
    "sql": "sql",
    "sqlite": "sqlite",
    "vertica": "vertica",
    None: "",
}


class AppSecSqliSubscriber(Subscriber[DbApiExecuteEvent]):
    """Subscriber for SQL injection (SQLi) detection on dbapi execute calls."""

    event_names = (DbApiExecuteEvent.event_name,)

    @classmethod
    def on_event(cls, event_instance: DbApiExecuteEvent) -> None:
        if not AppSecSpanProcessor.rasp_enabled("sqli"):
            return
        if not event_instance.query or not isinstance(event_instance.query, str):
            return
        if not in_asm_context():
            report_rasp_skipped(EXPLOIT_PREVENTION.TYPE.SQLI, False)
            return
        call_waf_callback(
            {
                EXPLOIT_PREVENTION.ADDRESS.SQLI: event_instance.query,
                EXPLOIT_PREVENTION.ADDRESS.SQLI_TYPE: DIALECT_FROM_SPAN.get(event_instance.span_name_prefix, ""),
            },
            crop_trace="AppSecSqliSubscriber",
            rule_type=EXPLOIT_PREVENTION.TYPE.SQLI,
        )
        if blocking_config := get_blocked():
            raise BlockingException(
                blocking_config, EXPLOIT_PREVENTION.BLOCKING, EXPLOIT_PREVENTION.TYPE.SQLI, event_instance.query
            )
