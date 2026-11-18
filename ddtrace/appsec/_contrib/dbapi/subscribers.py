from ddtrace.appsec._asm_request_context import call_waf_callback
from ddtrace.appsec._asm_request_context import get_blocked
from ddtrace.appsec._constants import EXPLOIT_PREVENTION
from ddtrace.appsec._ddwaf import DDWafSqlTokenizer
from ddtrace.appsec._rasp import _must_block
from ddtrace.appsec._rasp import get_rasp_capability
from ddtrace.contrib._events.dbapi import DbQueryEvent
from ddtrace.internal._exceptions import BlockingException
from ddtrace.internal.core.subscriber import Subscriber


_SPAN_NAME_PREFIX_TO_SQL_TOKENIZER: dict[str, DDWafSqlTokenizer] = {
    "mysql": DDWafSqlTokenizer.MYSQL,
    "oracle": DDWafSqlTokenizer.ORACLE,
    "postgres": DDWafSqlTokenizer.POSTGRESQL,
    "pymysql": DDWafSqlTokenizer.MYSQL,
    "sqlite": DDWafSqlTokenizer.SQLITE,
}


class AppSecDbApiSubscriber(Subscriber):
    auto_register = False
    event_names = (DbQueryEvent.event_name,)

    @classmethod
    def on_event(cls, event: DbQueryEvent) -> None:
        if not get_rasp_capability("sqli") or not event.query:
            return

        if isinstance(event.query, str):
            query = event.query
        else:
            # Best effort, so that the query is always inspected: undecodable bytes are replaced, and the query is
            # read as UTF-8 when the connection encoding cannot be resolved.
            try:
                encoding = event.get_encoding() if event.get_encoding else None
                # PostgreSQL reports SQL_ASCII as ascii, but it leaves the application's byte encoding unspecified.
                if encoding == "ascii":
                    encoding = "utf-8"
                query = str(event.query, encoding or "utf-8", "replace")
            except Exception:
                query = str(event.query, "utf-8", "replace")

        result = call_waf_callback(
            {
                EXPLOIT_PREVENTION.ADDRESS.SQLI: query,
                EXPLOIT_PREVENTION.ADDRESS.SQLI_TYPE: _SPAN_NAME_PREFIX_TO_SQL_TOKENIZER.get(
                    event.span_name_prefix, DDWafSqlTokenizer.GENERIC
                ).value,
            },
            crop_trace="on_event",
            rule_type=EXPLOIT_PREVENTION.TYPE.SQLI,
        )
        if result and _must_block(result.actions):
            raise BlockingException(get_blocked(), EXPLOIT_PREVENTION.BLOCKING, EXPLOIT_PREVENTION.TYPE.SQLI, query)
