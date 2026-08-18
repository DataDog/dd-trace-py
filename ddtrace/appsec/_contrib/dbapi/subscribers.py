from ddtrace.appsec._asm_request_context import call_waf_callback
from ddtrace.appsec._asm_request_context import get_blocked
from ddtrace.appsec._constants import EXPLOIT_PREVENTION
from ddtrace.appsec._ddwaf import DDWafSqlTokenizes
from ddtrace.appsec._rasp import _must_block
from ddtrace.appsec._rasp import get_rasp_capability
from ddtrace.contrib._events.dbapi import DbApiEvent
from ddtrace.internal._exceptions import BlockingException
from ddtrace.internal.core.subscriber import Subscriber


_SPAN_NAME_PREFIX_TO_SQL_TOKENIZER: dict[str, DDWafSqlTokenizes] = {
    "mysql": DDWafSqlTokenizes.MYSQL,
    "oracle": DDWafSqlTokenizes.ORACLE,
    "postgres": DDWafSqlTokenizes.POSTGRESQL,
    "pymysql": DDWafSqlTokenizes.MYSQL,
    "sqlite": DDWafSqlTokenizes.SQLITE,
}


class AppSecDbApiSubscriber(Subscriber):
    event_names = (DbApiEvent.event_name,)

    @classmethod
    def on_event(cls, event: DbApiEvent) -> None:
        if not get_rasp_capability("sqli") or not event.query:
            return

        result = call_waf_callback(
            {
                EXPLOIT_PREVENTION.ADDRESS.SQLI: event.query,
                EXPLOIT_PREVENTION.ADDRESS.SQLI_TYPE: _SPAN_NAME_PREFIX_TO_SQL_TOKENIZER.get(
                    event.span_name_prefix, DDWafSqlTokenizes.GENERIC
                ).value,
            },
            crop_trace="on_event",
            rule_type=EXPLOIT_PREVENTION.TYPE.SQLI,
        )
        if result and _must_block(result.actions):
            raise BlockingException(
                get_blocked(), EXPLOIT_PREVENTION.BLOCKING, EXPLOIT_PREVENTION.TYPE.SQLI, event.query
            )
