# stdlib
import json
from typing import Iterable

# 3p
import pymongo
from pymongo.message import _GetMore
from pymongo.message import _Query

# project
from ddtrace import config
from ddtrace._trace.pin import Pin
from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib import trace_utils
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.ext import db
from ddtrace.ext import mongo as mongox
from ddtrace.ext import net as netx
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.logger import get_logger
from ddtrace.trace import tracer


BATCH_PARTIAL_KEY = "Batch"

VERSION = pymongo.version_tuple

_CHECKOUT_FN_NAME = "get_socket" if pymongo.version_tuple < (4, 5) else "checkout"


if VERSION < (3, 6, 0):
    from pymongo.helpers import _unpack_response

log = get_logger(__name__)


def is_query(op):
    """Check if operation is a query. Shared between sync and async."""
    # NOTE: _Query should always have a spec field
    return hasattr(op, "spec")


def create_checkout_span(pin):
    """Create a span for socket checkout. Shared between sync and async."""
    span = tracer.trace(
        f"pymongo.{_CHECKOUT_FN_NAME}",
        service=trace_utils.ext_service(pin, config.pymongo),
        span_type=SpanTypes.MONGODB,
    )
    span._set_tag_str(COMPONENT, config.pymongo.integration_name)
    span._set_tag_str(db.SYSTEM, mongox.SERVICE)
    span._set_tag_str(SPAN_KIND, SpanKind.CLIENT)
    return span


def setup_checkout_span_tags(span, sock_info, instance):
    """Set up tags and metrics for checkout span. Shared between sync and async."""
    set_address_tags(span, sock_info.address)
    span.set_metric(_SPAN_MEASURED_KEY, 1)
    pin = Pin.get_from(instance)
    if pin:
        pin.onto(sock_info)


def process_server_operation_result(span, operation, result):
    """Process server operation result and set span tags/metrics. Shared between sync and async."""
    if result:
        if hasattr(result, "address"):
            set_address_tags(span, result.address)
        if is_query(operation) and hasattr(result, "docs"):
            set_query_rowcount(docs=result.docs, span=span)
    return result


def process_server_message_result(span, operation, result):
    """
    Process server message result and set span tags/metrics in synchronous clients.
    Only used in pymongo < 3.9.0.
    """
    if result:
        if hasattr(result, "address"):
            set_address_tags(span, result.address)
        if is_query(operation):
            if hasattr(result, "data"):
                if VERSION >= (3, 6, 0) and hasattr(result.data, "unpack_response"):
                    set_query_rowcount(docs=result.data.unpack_response(), span=span)
                else:
                    data = _unpack_response(response=result.data)
                    if VERSION < (3, 2, 0) and data.get("number_returned", None):
                        span.set_metric(db.ROWCOUNT, data.get("number_returned"))
                    elif (3, 2, 0) <= VERSION < (3, 6, 0):
                        docs = data.get("data", None)
                        set_query_rowcount(docs=docs, span=span)
    return result


def normalize_filter(f=None):
    """Normalize filter queries. Shared between sync and async."""
    if f is None:
        return {}
    if isinstance(f, list):
        return [normalize_filter(s) for s in f]
    if isinstance(f, dict):
        out = {}
        for k, v in f.items():
            if k == "$in" or k == "$nin":
                out[k] = "?"
            elif isinstance(v, (list, dict)):
                out[k] = normalize_filter(v)
            else:
                out[k] = "?"
        return out
    return {}


def set_address_tags(span, address):
    """Set address tags on span. Shared between sync and async."""
    if address:
        span._set_tag_str(netx.TARGET_HOST, address[0])
        span._set_tag_str(netx.SERVER_ADDRESS, address[0])
        span.set_tag(netx.TARGET_PORT, address[1])


def set_query_metadata(span, cmd):
    """Set span `mongodb.query` tag and resource given command query. Shared between sync and async."""
    if cmd.query:
        nq = normalize_filter(cmd.query)
        q = json.dumps(nq)
        span.set_tag("mongodb.query", q)
        span.resource = "{} {} {}".format(cmd.name, cmd.coll, q)
    else:
        span.resource = "{} {}".format(cmd.name, cmd.coll)


def set_query_rowcount(docs, span):
    """Set query row count metric on span. Shared between sync and async."""
    # results returned in batches, get len of each batch
    cursor = None
    if isinstance(docs, Iterable):
        try:
            docs_list = list(docs) if not isinstance(docs, (list, tuple)) else docs
            if len(docs_list) > 0 and isinstance(docs_list[0], dict):
                cursor = docs_list[0].get("cursor", None)
        except (TypeError, ValueError):
            # If docs is not iterable or can't be converted, skip
            pass
    if cursor and isinstance(cursor, dict):
        rowcount = sum(len(documents) for batch_key, documents in cursor.items() if BATCH_PARTIAL_KEY in batch_key)
        span.set_metric(db.ROWCOUNT, rowcount)


def dbm_dispatch(span, args, kwargs):
    """Dispatch DBM (Database Monitoring). Shared between sync and async."""
    result = core.dispatch_with_results(  # ast-grep-ignore: core-dispatch-with-results
        "pymongo.execute", (config.pymongo, span, args, kwargs)
    ).result
    if result:
        span, args, kwargs = result.value
    return span, args, kwargs


def dbm_comment_injector(dbm_comment, command):
    """Inject DBM comment into command. Shared between sync and async."""
    try:
        if VERSION < (3, 9):
            log.debug("DBM propagation not supported for PyMongo versions < 3.9")
            return command

        if dbm_comment is not None:
            dbm_comment = dbm_comment.strip()
        if isinstance(command, _Query):
            if is_query(command):
                if "$query" not in command.spec:
                    command.spec = {"$query": command.spec}
                command.spec = dbm_comment_injector(dbm_comment, command.spec)
        elif isinstance(command, _GetMore):
            if hasattr(command, "comment"):
                command.comment = dbm_merge_comment(command.comment, dbm_comment)
        else:
            comment_exists = False
            for comment_key in ("comment", "$comment"):
                if comment_key in command:
                    command[comment_key] = dbm_merge_comment(command[comment_key], dbm_comment)
                    comment_exists = True
            if not comment_exists:
                command["comment"] = dbm_comment
        return command
    except (TypeError, ValueError):
        log.warning(
            "Linking Database Monitoring profiles to spans is not supported for query type: %s. "
            "To disable this feature, set DD_DBM_PROPAGATION_MODE=disabled",
            type(command),
        )
    return command


def dbm_merge_comment(existing_comment, dbm_comment):
    """Merge DBM comment with existing comment. Shared between sync and async."""
    if existing_comment is None:
        return dbm_comment
    if isinstance(existing_comment, str):
        return existing_comment + "," + dbm_comment
    if isinstance(existing_comment, list):
        existing_comment.append(dbm_comment)
        return existing_comment
    return existing_comment
