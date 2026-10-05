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
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.wrapping import unwrap as _u
from ddtrace.internal.wrapping import wrap as _w
from ddtrace.trace import tracer

from .client import datadog_trace_operation
from .client import dbm_dispatch_operation
from .client import parse_bulk_write_command
from .client import parse_socket_command_spec
from .client import parse_socket_write_command_msg
from .client import trace_cmd
from .utils import create_checkout_span
from .utils import dbm_dispatch
from .utils import process_server_operation_result
from .utils import process_write_command_result
from .utils import setup_checkout_span_tags


log = get_logger(__name__)


VERSION = pymongo.version_tuple


async def trace_async_server_run_operation(func: FunctionType, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    """Trace operations on AsyncServer or, from 4.18, AsyncMongoClient."""
    operation = get_argument_value(args, kwargs, 1 if VERSION >= (4, 18) else 2, "operation")

    span = datadog_trace_operation(operation)
    if span is None:
        return await func(*args, **kwargs)
    with span:
        span, args, kwargs = dbm_dispatch_operation(span, args, kwargs)
        result = await func(*args, **kwargs)
        return process_server_operation_result(span, operation, result)


async def trace_async_server_checkout(func: FunctionType, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    """Wrapper for AsyncServer.checkout to trace socket checkout.

    AsyncServer.checkout() returns an async context manager. We wrap it to add tracing.
    """
    # Call the original async function which returns an async context manager
    cm = await func(*args, **kwargs)
    return trace_async_checkout(cm)


def trace_async_mongo_client_checkout(func: FunctionType, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    """Trace 4.18 client checkout methods, which return context managers directly."""
    return trace_async_checkout(func(*args, **kwargs))


def trace_async_checkout(cm: Any) -> Any:
    """Trace checkout while preserving the context manager's yielded value."""

    if not tracer.enabled:
        return cm

    @contextlib.asynccontextmanager
    async def traced_cm():
        with create_checkout_span() as span:
            async with cm as sock_info:
                setup_checkout_span_tags(span, sock_info, None)
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
    """Trace connection writes or, from 4.18, bulk write batches."""
    parsed = (
        parse_bulk_write_command(args, kwargs) if VERSION >= (4, 18) else parse_socket_write_command_msg(args, kwargs)
    )
    if parsed is None:
        return await func(*args, **kwargs)

    socket_instance, cmd = parsed
    async with async_trace_cmd(cmd, socket_instance, socket_instance.address) as s:
        if VERSION >= (4, 18):
            s, args, kwargs = dbm_dispatch(s, args, kwargs)
        result = await func(*args, **kwargs)
        return process_write_command_result(s, result)


_ASYNC_MIN_VERSION = (4, 12)


def _check_async_support():
    """Check if async pymongo support is available."""
    if VERSION < _ASYNC_MIN_VERSION:
        log.warning("Async pymongo support requires pymongo >= %s", ".".join(map(str, _ASYNC_MIN_VERSION)))
        return False
    return True


def patch_pymongo_async_modules():
    """Patch asynchronous pymongo modules."""
    _set_pymongo_async_wrappers(_w)


def unpatch_pymongo_async_modules():
    """Unpatch asynchronous pymongo modules."""
    _set_pymongo_async_wrappers(_u)


def _set_pymongo_async_wrappers(wrap):
    if not _check_async_support():
        return
    if VERSION >= (4, 18):
        wrap(AsyncMongoClient._run_operation, trace_async_server_run_operation)
        wrap(AsyncMongoClient._conn_from_server, trace_async_mongo_client_checkout)
        wrap(AsyncMongoClient._checkout, trace_async_mongo_client_checkout)
    else:
        wrap(AsyncServer.run_operation, trace_async_server_run_operation)
        wrap(AsyncServer.checkout, trace_async_server_checkout)
    wrap(AsyncConnection.command, trace_async_socket_command)
    if VERSION >= (4, 18):
        wrap(_AsyncBulk._execute_batch, trace_async_socket_write_command)
        wrap(_AsyncBulk._execute_batch_unack, trace_async_socket_write_command)
    else:
        wrap(AsyncConnection.write_command, trace_async_socket_write_command)
