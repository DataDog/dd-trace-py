# stdlib
import contextlib
from types import FunctionType
from typing import Any

import pymongo
from pymongo.asynchronous.mongo_client import AsyncMongoClient
from pymongo.asynchronous.pool import AsyncConnection
from pymongo.asynchronous.server import Server as AsyncServer


if pymongo.version_tuple >= (4, 18):
    from pymongo.asynchronous.bulk import _AsyncBulk

# project
from ddtrace.ext import db
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.wrapping import unwrap as _u
from ddtrace.internal.wrapping import wrap as _w
from ddtrace.trace import tracer

from .client import datadog_trace_operation
from .client import dbm_dispatch_mongo_client_run_operation
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


async def trace_async_server_run_operation(func: FunctionType, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    """Wrapper for AsyncServer.run_operation to trace operations."""
    operation = get_argument_value(args, kwargs, 2, "operation")

    span = datadog_trace_operation(operation)
    if span is None:
        return await func(*args, **kwargs)
    with span:
        span, args, kwargs = dbm_dispatch(span, args, kwargs)
        result = await func(*args, **kwargs)
        return process_server_operation_result(span, operation, result)


async def trace_async_mongo_client_run_operation(
    func: FunctionType, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    """Wrapper for AsyncMongoClient._run_operation to trace operations."""
    operation = get_argument_value(args, kwargs, 1, "operation")

    span = datadog_trace_operation(operation)
    if span is None:
        return await func(*args, **kwargs)
    with span:
        span, args, kwargs = dbm_dispatch_mongo_client_run_operation(span, args, kwargs)
        result = await func(*args, **kwargs)
        return process_server_operation_result(span, operation, result)


async def trace_async_server_checkout(func: FunctionType, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    """Wrapper for AsyncServer.checkout to trace socket checkout.

    AsyncServer.checkout() returns an async context manager. We wrap it to add tracing.
    """
    instance = get_argument_value(args, kwargs, 0, "self")

    # Call the original async function which returns an async context manager
    cm = await func(*args, **kwargs)

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


def trace_async_mongo_client_checkout(func: FunctionType, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    """Wrapper for AsyncMongoClient._checkout to trace socket checkout."""
    cm = func(*args, **kwargs)

    if not tracer.enabled:
        return cm

    @contextlib.asynccontextmanager
    async def traced_cm():
        with create_checkout_span() as span:
            async with cm as sock_info:
                setup_checkout_span_tags(span, sock_info, None)
                yield sock_info

    return traced_cm()


def trace_async_mongo_client_read_checkout(func: FunctionType, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    """Wrapper for AsyncMongoClient._conn_from_server to trace socket checkout."""
    cm = func(*args, **kwargs)

    if not tracer.enabled:
        return cm

    @contextlib.asynccontextmanager
    async def traced_cm():
        with create_checkout_span() as span:
            async with cm as (sock_info, read_preference):
                setup_checkout_span_tags(span, sock_info, None)
                yield sock_info, read_preference

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


async def trace_async_bulk_execute_batch(func: FunctionType, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    """Wrapper for PyMongo 4.18 async bulk write commands."""
    parsed = parse_bulk_write_command(args, kwargs)
    if parsed is None:
        return await func(*args, **kwargs)

    bulk_write_context, cmd = parsed
    socket_instance = bulk_write_context.conn
    async with async_trace_cmd(cmd, socket_instance, socket_instance.address) as s:
        s, args, kwargs = dbm_dispatch(s, args, kwargs)
        result = await func(*args, **kwargs)
        if isinstance(result, tuple) and result[0]:
            s._set_attribute(db.ROWCOUNT, result[0].get("n", -1))
        elif result:
            s._set_attribute(db.ROWCOUNT, result.get("n", -1))
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
        _w(AsyncMongoClient._run_operation, trace_async_mongo_client_run_operation)
        _w(AsyncMongoClient._conn_from_server, trace_async_mongo_client_read_checkout)
    else:
        _w(AsyncServer.run_operation, trace_async_server_run_operation)
    if VERSION >= (4, 18):
        _w(AsyncMongoClient._checkout, trace_async_mongo_client_checkout)
    else:
        _w(AsyncServer.checkout, trace_async_server_checkout)
    _w(AsyncConnection.command, trace_async_socket_command)
    if VERSION >= (4, 18):
        _w(_AsyncBulk._execute_batch, trace_async_bulk_execute_batch)
        _w(_AsyncBulk._execute_batch_unack, trace_async_bulk_execute_batch)
    else:
        _w(AsyncConnection.write_command, trace_async_socket_write_command)


def unpatch_pymongo_async_modules():
    """Unpatch asynchronous pymongo modules."""
    if not _check_async_support():
        return
    if VERSION >= (4, 18):
        _u(AsyncMongoClient._run_operation, trace_async_mongo_client_run_operation)
        _u(AsyncMongoClient._conn_from_server, trace_async_mongo_client_read_checkout)
    else:
        _u(AsyncServer.run_operation, trace_async_server_run_operation)
    if VERSION >= (4, 18):
        _u(AsyncMongoClient._checkout, trace_async_mongo_client_checkout)
    else:
        _u(AsyncServer.checkout, trace_async_server_checkout)
    _u(AsyncConnection.command, trace_async_socket_command)
    if VERSION >= (4, 18):
        _u(_AsyncBulk._execute_batch, trace_async_bulk_execute_batch)
        _u(_AsyncBulk._execute_batch_unack, trace_async_bulk_execute_batch)
    else:
        _u(AsyncConnection.write_command, trace_async_socket_write_command)
