# stdlib
import contextlib
from types import FunctionType
from typing import Any

import pymongo

# project
from ddtrace.ext import db
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.wrapping import unwrap as _u
from ddtrace.internal.wrapping import wrap as _w
from ddtrace.trace import tracer

from .client import datadog_trace_operation
from .client import parse_bulk_write_command
from .client import parse_socket_command_spec
from .client import parse_socket_write_command_msg
from .client import trace_cmd
from .utils import create_checkout_span
from .utils import dbm_dispatch
from .utils import process_server_operation_result
from .utils import setup_checkout_span_tags


log = get_logger(__name__)


VERSION = pymongo.version_tuple


if VERSION >= (4, 18):
    from pymongo.asynchronous.command_runner import run_bulk_write_command as async_run_bulk_write_command
    from pymongo.asynchronous.cursor_base import _AsyncCursorBase
    from pymongo.asynchronous.pool import AsyncConnection
    from pymongo.asynchronous.pool import Pool as AsyncPool
else:
    from pymongo.asynchronous.pool import AsyncConnection
    from pymongo.asynchronous.server import Server as AsyncServer


async def trace_async_server_run_operation(func: FunctionType, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    """Wrapper for AsyncServer.run_operation to trace operations."""
    server_instance = get_argument_value(args, kwargs, 0, "self")
    operation = get_argument_value(args, kwargs, 2, "operation")

    span = datadog_trace_operation(operation, server_instance)
    if span is None:
        return await func(*args, **kwargs)
    with span:
        span, args, kwargs = dbm_dispatch(span, args, kwargs)
        result = await func(*args, **kwargs)
        return process_server_operation_result(span, operation, result)


# AIDEV-NOTE: AsyncServer.checkout is awaitable through PyMongo 4.17. AsyncPool.checkout
# in 4.18+ is synchronous, returns an async context manager, and is also used by SDAM
# monitor pools. Keep separate wrappers and do not trace monitor pool checkouts.
async def trace_async_server_checkout(func: FunctionType, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    """Wrapper for AsyncServer.checkout to trace socket checkout.

    AsyncServer.checkout() returns an async context manager. We wrap it to add tracing.
    """
    instance = get_argument_value(args, kwargs, 0, "self")

    # Call the original async function which returns an async context manager
    cm = await func(*args, **kwargs)

    return _trace_async_checkout_context_manager(cm, instance)


def trace_async_pool_checkout(func: FunctionType, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    """Wrapper for AsyncPool.checkout to trace socket checkout."""
    instance = get_argument_value(args, kwargs, 0, "self")
    cm = func(*args, **kwargs)
    if getattr(instance, "is_sdam", False):
        return cm
    return _trace_async_checkout_context_manager(cm, instance)


def _trace_async_checkout_context_manager(cm: Any, instance: Any) -> Any:
    """Wrap an async checkout context manager with tracing."""
    if not tracer.enabled:
        # Return the original context manager unchanged
        return cm

    @contextlib.asynccontextmanager
    async def traced_cm():
        with create_checkout_span() as span:
            async with cm as sock_info:
                setup_checkout_span_tags(span, sock_info, instance)
                yield sock_info

    return traced_cm()


async def trace_async_socket_command(func: FunctionType, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    """Wrapper for AsyncConnection.command to trace command operations."""
    parsed = parse_socket_command_spec(args, kwargs)
    if parsed is None:
        return await func(*args, **kwargs)

    socket_instance, dbname, cmd = parsed
    async with async_trace_cmd(cmd, socket_instance, socket_instance.address) as s:
        s, args, kwargs = dbm_dispatch(s, args, kwargs)
        return await func(*args, **kwargs)


@contextlib.asynccontextmanager
async def async_trace_cmd(cmd, socket_instance, address):
    """Async context manager wrapper for trace_cmd that properly handles GeneratorExit."""
    with trace_cmd(cmd, socket_instance, address) as span:
        yield span


async def trace_async_socket_write_command(func: FunctionType, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    """Wrapper for AsyncConnection.write_command to trace write command operations."""
    parsed = parse_socket_write_command_msg(args, kwargs)
    if parsed is None:
        return await func(*args, **kwargs)

    socket_instance, cmd = parsed
    async with async_trace_cmd(cmd, socket_instance, socket_instance.address) as s:
        result = await func(*args, **kwargs)
        if result:
            s._set_attribute(db.ROWCOUNT, result.get("n", -1))
        return result


async def trace_async_bulk_write_command(func: FunctionType, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    """Trace acknowledged bulk writes through PyMongo 4.18's command runner."""
    if kwargs.get("unacknowledged", False):
        return await func(*args, **kwargs)

    parsed = parse_bulk_write_command(args, kwargs)
    if parsed is None:
        return await func(*args, **kwargs)

    socket_instance, cmd = parsed
    async with async_trace_cmd(cmd, socket_instance, socket_instance.address) as s:
        result = await func(*args, **kwargs)
        result_docs = result[0] if result else None
        if result_docs:
            s._set_attribute(db.ROWCOUNT, result_docs[0].get("n", -1))
        return result


_ASYNC_MIN_VERSION = (4, 12)


def _check_async_support():
    """Check if async pymongo support is available."""
    if VERSION < _ASYNC_MIN_VERSION:
        log.warning("Async pymongo support requires pymongo >= %s", ".".join(map(str, _ASYNC_MIN_VERSION)))
        return False
    return True


def patch_pymongo_async_modules():
    """Patch asynchronous pymongo modules."""
    if not _check_async_support():
        return

    if VERSION >= (4, 18):
        _w(_AsyncCursorBase._run_with_conn, trace_async_server_run_operation)
        _w(AsyncPool.checkout, trace_async_pool_checkout)
    else:
        _w(AsyncServer.run_operation, trace_async_server_run_operation)
        _w(AsyncServer.checkout, trace_async_server_checkout)

    _w(AsyncConnection.command, trace_async_socket_command)
    if VERSION >= (4, 18):
        _w(async_run_bulk_write_command, trace_async_bulk_write_command)
    else:
        _w(AsyncConnection.write_command, trace_async_socket_write_command)


def unpatch_pymongo_async_modules():
    """Unpatch asynchronous pymongo modules."""
    if not _check_async_support():
        return

    if VERSION >= (4, 18):
        _u(_AsyncCursorBase._run_with_conn, trace_async_server_run_operation)
        _u(AsyncPool.checkout, trace_async_pool_checkout)
    else:
        _u(AsyncServer.run_operation, trace_async_server_run_operation)
        _u(AsyncServer.checkout, trace_async_server_checkout)

    _u(AsyncConnection.command, trace_async_socket_command)
    if VERSION >= (4, 18):
        _u(async_run_bulk_write_command, trace_async_bulk_write_command)
    else:
        _u(AsyncConnection.write_command, trace_async_socket_write_command)
