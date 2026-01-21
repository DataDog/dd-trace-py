# stdlib
import contextlib
from types import FunctionType
from typing import Any
from typing import Dict
from typing import Tuple

import pymongo
from pymongo.asynchronous.mongo_client import AsyncMongoClient
from pymongo.asynchronous.pool import AsyncConnection
from pymongo.asynchronous.server import Server as AsyncServer
from pymongo.asynchronous.topology import Topology as AsyncTopology

# project
from ddtrace._trace.pin import Pin
from ddtrace.ext import db
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.wrapping import unwrap as _u
from ddtrace.internal.wrapping import wrap as _w

from .client import _CHECKOUT_FN_NAME
from .client import datadog_trace_operation
from .client import parse_socket_command_spec
from .client import parse_socket_write_command_msg
from .client import propagate_pin_to_server
from .client import setup_mongo_client_pin
from .client import trace_cmd
from .utils import create_checkout_span
from .utils import dbm_dispatch
from .utils import process_server_operation_result
from .utils import setup_checkout_span_tags


log = get_logger(__name__)


VERSION = pymongo.version_tuple


def trace_async_mongo_client_init(func: FunctionType, args: Tuple[Any, ...], kwargs: Dict[str, Any]) -> None:
    """Wrapper for AsyncMongoClient.__init__ to set up pin handling."""
    func(*args, **kwargs)
    client = get_argument_value(args, kwargs, 0, "self")
    setup_mongo_client_pin(client)


async def trace_async_topology_select_server(func: FunctionType, args: Tuple[Any, ...], kwargs: Dict[str, Any]) -> Any:
    """Wrapper for AsyncTopology.select_server to propagate pin to selected server."""
    # AsyncTopology.select_server is async, so we need to await it
    server = await func(*args, **kwargs)
    # Ensure the pin used on the traced mongo client is passed down to the topology instance
    # This allows us to pass the same pin in traced server objects.
    topology_instance = get_argument_value(args, kwargs, 0, "self")
    propagate_pin_to_server(server, topology_instance)
    return server


async def trace_async_server_run_operation_and_with_response(
    func: FunctionType, args: Tuple[Any, ...], kwargs: Dict[str, Any]
) -> Any:
    """
    Wrapper for AsyncServer.run_operation to trace operations.

    Extracts operation from args[2] (pattern: run_operation(self, sock_info, operation, ...)).
    """
    server_instance = get_argument_value(args, kwargs, 0, "self")
    operation = get_argument_value(args, kwargs, 2, "operation")

    span = datadog_trace_operation(operation, server_instance)
    if span is None:
        return await func(*args, **kwargs)
    with span:
        span, args, kwargs = dbm_dispatch(span, args, kwargs)
        result = await func(*args, **kwargs)
        return process_server_operation_result(span, operation, result)


async def traced_async_get_socket(func: FunctionType, args: Tuple[Any, ...], kwargs: Dict[str, Any]) -> Any:
    """
    Async wrapper for AsyncServer.checkout to trace socket checkout.

    checkout() is an async function that returns an async context manager.
    We wrap the returned context manager to add tracing.
    """
    instance = get_argument_value(args, kwargs, 0, "self")
    pin = Pin.get_from(instance)

    # Call the original async function which returns an async context manager
    cm = await func(*args, **kwargs)

    if not pin or not pin.enabled():
        # Return the original context manager unchanged
        return cm

    # Wrap the context manager to add tracing
    @contextlib.asynccontextmanager
    async def traced_cm():
        with create_checkout_span(pin, _CHECKOUT_FN_NAME) as span:
            async with cm as sock_info:
                setup_checkout_span_tags(span, sock_info, instance)
                yield sock_info

    return traced_cm()


async def trace_async_socket_command(func: FunctionType, args: Tuple[Any, ...], kwargs: Dict[str, Any]) -> Any:
    """
    Wrapper for AsyncConnection.command to trace command operations.

    Uses parse_socket_command_spec to extract dbname (args[1]) and spec (args[2]).
    """
    parsed = parse_socket_command_spec(args, kwargs)
    if parsed is None:
        return await func(*args, **kwargs)

    socket_instance, dbname, cmd, pin = parsed
    with trace_cmd(cmd, socket_instance, socket_instance.address) as s:
        # dispatch DBM
        s, args, kwargs = dbm_dispatch(s, args, kwargs)
        return await func(*args, **kwargs)


async def trace_async_socket_write_command(func: FunctionType, args: Tuple[Any, ...], kwargs: Dict[str, Any]) -> Any:
    """
    Wrapper for AsyncConnection.write_command to trace write command operations.

    Uses parse_socket_write_command_msg to extract msg (args[2]).
    Signature: write_command(self, request_id, msg, codec_options)
    """
    parsed = parse_socket_write_command_msg(args, kwargs)
    if parsed is None:
        return await func(*args, **kwargs)

    socket_instance, cmd, pin = parsed
    with trace_cmd(cmd, socket_instance, socket_instance.address) as s:
        result = await func(*args, **kwargs)
        if result:
            s.set_metric(db.ROWCOUNT, result.get("n", -1))
        return result


def patch_pymongo_async_modules():
    """Patch asynchronous pymongo modules."""
    if VERSION < (4, 12):
        log.warning("Async pymongo support requires pymongo >= 4.12")
        return
    # All wrappers verified to match sync equivalents - see ASYNC_WRAPPER_REVIEW.md
    _w(AsyncMongoClient.__init__, trace_async_mongo_client_init)
    _w(AsyncTopology.select_server, trace_async_topology_select_server)
    _w(AsyncServer.run_operation, trace_async_server_run_operation_and_with_response)
    _w(AsyncServer.checkout, traced_async_get_socket)
    _w(AsyncConnection.command, trace_async_socket_command)
    _w(AsyncConnection.write_command, trace_async_socket_write_command)


def unpatch_pymongo_async_modules():
    """Unpatch asynchronous pymongo modules."""
    if VERSION < (4, 12):
        log.warning("Async pymongo support requires pymongo >= 4.12")
        return
    _u(AsyncMongoClient.__init__, trace_async_mongo_client_init)
    _u(AsyncTopology.select_server, trace_async_topology_select_server)
    _u(AsyncServer.run_operation, trace_async_server_run_operation_and_with_response)
    _u(AsyncServer.checkout, traced_async_get_socket)
    _u(AsyncConnection.command, trace_async_socket_command)
    _u(AsyncConnection.write_command, trace_async_socket_write_command)
