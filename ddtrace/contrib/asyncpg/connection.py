from ...ext import sql

from ddtrace import Pin
from ddtrace.vendor import wrapt


async def _trace_method(method, pin, trace_name, query, rowcount_method, extra_tags, *args, **kwargs):
    if not pin or not pin.enabled():
        result = await method(*args, **kwargs)
        return result

    service = pin.service

    with pin.tracer.trace(trace_name, service=service, resource=query) as s:
        s.span_type = sql.TYPE
        s.set_tags(pin.tags)
        s.set_tags(extra_tags)

        result = await method(*args, **kwargs)

        if rowcount_method:
            rowcount_method(s, result)

        return result


def _fetch_rowcount(span, result):
    span.set_metric("db.rowcount", len(result))


def _fetchrow_rowcount(span, result):
    span.set_metric("db.rowcount", 1 if result is not None else 0)


def _execute_rowcount(span, result):
    span.set_metric("db.rowcount", len(result[0]))


def _forward_rowcount(span, result):
    span.set_metric("db.rowcount", result)


class AIOTracedProtocol(wrapt.ObjectProxy):
    def __init__(self, proto, pin):
        super().__init__(proto)
        pin.onto(self)
        self._self_name = pin.app or "sql"

    async def _trace_method(self, method, query, rowcount_method, extra_tags, *args, **kwargs):
        pin = Pin.get_from(self)

        result = await _trace_method(
            method, pin, self._self_name + "." + method.__name__, query, rowcount_method, extra_tags, *args, **kwargs
        )

        return result

    async def prepare(self, stmt_name, query, timeout, *args, **kwargs):
        result = await self._trace_method(
            self.__wrapped__.prepare, query, None, {}, stmt_name, query, timeout, *args, **kwargs
        )
        return result

    async def bind_execute(self, state, args, portal_name, limit, return_extra, timeout):
        result = await self._trace_method(
            self.__wrapped__.bind_execute,
            state.query,
            _fetch_rowcount,
            {},
            state,
            args,
            portal_name,
            limit,
            return_extra,
            timeout,
        )
        return result

    async def bind_execute_many(self, state, args, portal_name, return_extra, timeout):
        result = await self._trace_method(
            self.__wrapped__.bind_execute_many,
            state.query,
            None,
            {"sql.executemany": "true"},
            state,
            args,
            portal_name,
            return_extra,
            timeout,
        )
        return result

    async def bind(self, state, args, portal_name, timeout):
        result = await self._trace_method(
            self.__wrapped__.bind, state.query, None, {}, state, args, portal_name, timeout
        )
        return result

    async def execute(self, state, portal_name, limit, return_extra, timeout):
        result = await self._trace_method(
            self.__wrapped__.execute,
            state.query,
            _execute_rowcount,
            {},
            state,
            portal_name,
            limit,
            return_extra,
            timeout,
        )
        return result

    async def query(self, query, timeout):
        result = await self._trace_method(self.__wrapped__.query, query, None, {}, query, timeout)
        return result

    async def copy_out(self, copy_stmt, sink, timeout):
        result = await self._trace_method(self.__wrapped__.copy_out, copy_stmt, None, {}, copy_stmt, sink, timeout)
        return result

    async def copy_in(self, copy_stmt, reader, data, records, record_stmt, timeout):
        result = await self._trace_method(
            self.__wrapped__.copy_in, copy_stmt, None, {}, copy_stmt, reader, data, records, record_stmt, timeout
        )
        return result

    async def close_statement(self, state, timeout):
        result = await self._trace_method(self.__wrapped__.close_statement, state.query, None, {}, state, timeout)
        return result

    async def close(self, timeout):
        result = await self._trace_method(self.__wrapped__.close, "", None, {}, timeout)
        return result
