import gc

from mock import Mock
import pytest

from ddtrace._trace.span import Span
from ddtrace.contrib.celery.utils import attach_span
from ddtrace.contrib.celery.utils import detach_span
from ddtrace.contrib.celery.utils import retrieve_span
from ddtrace.contrib.celery.utils import retrieve_task_id
from ddtrace.contrib.celery.utils import set_tags_from_context


@pytest.fixture
def span(tracer):
    yield tracer.trace("test")


@pytest.fixture
def fn_task():
    _ = Mock()
    _.__dd_task_span = None
    yield _


def test_span_propagation(span, fn_task):
    # ensure spans getter and setter works properly
    # propagate and retrieve a Span
    task_id = "7c6731af-9533-40c3-83a9-25b58f0d837f"
    attach_span(fn_task, task_id, span)
    span_after = retrieve_span(fn_task, task_id)
    assert span is span_after


def test_span_delete(span, fn_task):
    # ensure the helper removes properly a propagated Span
    # propagate a Span
    task_id = "7c6731af-9533-40c3-83a9-25b58f0d837f"
    attach_span(fn_task, task_id, span)
    # delete the Span
    weak_dict = fn_task.__dd_task_span
    detach_span(fn_task, task_id)
    assert weak_dict.get((task_id, False)) is None


def test_span_delete_empty(fn_task):
    # ensure the helper works even if the Task doesn't have
    # a propagation
    # delete the Span
    exception = None
    task_id = "7c6731af-9533-40c3-83a9-25b58f0d837f"
    try:
        detach_span(fn_task, task_id)
    except Exception as e:
        exception = e
    assert exception is None


def test_memory_leak_safety(tracer, fn_task):
    # Spans are shared between signals using a Dictionary (task_id -> span).
    # This test ensures the GC correctly cleans finished spans. If this test
    # fails a memory leak will happen for sure.
    # propagate and finish a Span for `fn_task`
    task_id = "7c6731af-9533-40c3-83a9-25b58f0d837f"
    attach_span(fn_task, task_id, tracer.trace("celery.run"))
    weak_dict = fn_task.__dd_task_span
    key = (task_id, False)
    assert weak_dict.get(key)
    # flush data and force the GC
    weak_dict.get(key).finish()
    tracer.pop()
    tracer.pop_traces()
    gc.collect()
    assert weak_dict.get(key) is None


def test_tags_from_context():
    # it should extract only relevant keys
    context = {
        "correlation_id": "44b7f305",
        "delivery_info": {"eager": "True", "priority": "0", "int_zero": 0},
        "eta": "soon",
        "expires": "later",
        "hostname": "localhost",
        "id": "44b7f305",
        "reply_to": "44b7f305",
        "retries": 4,
        "timelimit": ("now", "later"),
        "custom_meta": "custom_value",
    }

    span = Span("test")
    set_tags_from_context(span, context)
    metas = span.get_tags()
    metrics = span.get_metrics()
    sentinel = object()
    assert metas["celery.correlation_id"] == "44b7f305"
    assert metas["celery.delivery_info.eager"] == "True"
    assert metas["celery.delivery_info.priority"] == "0"
    assert metrics["celery.delivery_info.int_zero"] == 0
    assert metas["celery.eta"] == "soon"
    assert metas["celery.expires"] == "later"
    assert metas["celery.hostname"] == "localhost"
    assert metas["celery.id"] == "44b7f305"
    assert metas["celery.reply_to"] == "44b7f305"
    assert metrics["celery.retries"] == 4
    assert metas["celery.timelimit"] == "('now', 'later')"
    assert metas.get("custom_meta", sentinel) is sentinel
    assert metrics.get("custom_metric", sentinel) is sentinel


def test_tags_from_context_empty_keys():
    # it should not extract empty keys
    span = Span("test")
    context = {
        "correlation_id": None,
        "exchange": "",
        "timelimit": (None, None),
        "retries": 0,
    }
    tags = span.get_tags()

    set_tags_from_context(span, context)
    assert {} == tags
    # edge case: `timelimit` can also be a list of None values
    context = {
        "timelimit": [None, None],
    }

    set_tags_from_context(span, context)
    assert {} == tags


def test_task_id_from_protocol_v1_no_headers():
    # ensures a `task_id` is properly returned when Protocol v1 is used.
    # `context` is an example of an emitted Signal with Protocol v1
    # this test assumes the headers are blank
    test_id = "dffcaec1-dd92-4a1a-b3ab-d6512f4beeb7"
    context = {
        "body": {
            "expires": None,
            "utc": True,
            "args": ["user"],
            "chord": None,
            "callbacks": None,
            "errbacks": None,
            "taskset": None,
            "id": test_id,
            "retries": 0,
            "task": "tests.contrib.celery.test_integration.fn_task_parameters",
            "timelimit": (None, None),
            "eta": None,
            "kwargs": {"force_logout": True},
        },
        "sender": "tests.contrib.celery.test_integration.fn_task_parameters",
        "exchange": "celery",
        "routing_key": "celery",
        "retry_policy": None,
        "headers": {},
        "properties": {},
    }

    task_id = retrieve_task_id(context)
    assert task_id == test_id


def test_task_id_from_protocol_v1_with_headers():
    # ensures a `task_id` is properly returned when Protocol v1 is used with headers.
    # `context` is an example of an emitted Signal with Protocol v1
    # this tests ensures that the headers have other information
    test_id = "dffcaec1-dd92-4a1a-b3ab-d6512f4beeb7"
    context = {
        "body": {
            "expires": None,
            "utc": True,
            "args": ["user"],
            "chord": None,
            "callbacks": None,
            "errbacks": None,
            "taskset": None,
            "id": test_id,
            "retries": 0,
            "task": "tests.contrib.celery.test_integration.fn_task_parameters",
            "timelimit": (None, None),
            "eta": None,
            "kwargs": {"force_logout": True},
        },
        "sender": "tests.contrib.celery.test_integration.fn_task_parameters",
        "exchange": "celery",
        "routing_key": "celery",
        "retry_policy": None,
        "headers": {
            "header1": "value",
            "header2": "value",
        },
        "properties": {},
    }

    task_id = retrieve_task_id(context)
    assert task_id == test_id


def test_task_id_from_protocol_v2_no_body():
    # ensures a `task_id` is properly returned when Protocol v2 is used.
    # `context` is an example of an emitted Signal with Protocol v2
    # this tests assumes the body has no data
    test_id = "7e917b83-4018-431d-9832-73a28e1fb6c0"
    context = {
        "body": {},
        "sender": "tests.contrib.celery.test_integration.fn_task_parameters",
        "exchange": "",
        "routing_key": "celery",
        "retry_policy": None,
        "headers": {
            "origin": "gen83744@hostname",
            "root_id": test_id,
            "expires": None,
            "shadow": None,
            "id": test_id,
            "kwargsrepr": "{'force_logout': True}",
            "lang": "py",
            "retries": 0,
            "task": "tests.contrib.celery.test_integration.fn_task_parameters",
            "group": None,
            "timelimit": [None, None],
            "parent_id": None,
            "argsrepr": "['user']",
            "eta": None,
        },
        "properties": {
            "reply_to": "c3054a07-5b28-3855-b18c-1623a24aaeca",
            "correlation_id": test_id,
        },
    }

    task_id = retrieve_task_id(context)
    assert task_id == test_id


def test_task_id_from_protocol_v2_with_body():
    # ensures a `task_id` is properly returned when Protocol v2 is used.
    # `context` is an example of an emitted Signal with Protocol v2
    # this tests assumes the body has data
    test_id = "7e917b83-4018-431d-9832-73a28e1fb6c0"
    context = {
        "body": (
            ["user"],
            {"force_logout": True},
            {"chord": None, "callbacks": None, "errbacks": None, "chain": None},
        ),
        "sender": "tests.contrib.celery.test_integration.fn_task_parameters",
        "exchange": "",
        "routing_key": "celery",
        "retry_policy": None,
        "headers": {
            "origin": "gen83744@hostname",
            "root_id": test_id,
            "expires": None,
            "shadow": None,
            "id": test_id,
            "kwargsrepr": "{'force_logout': True}",
            "lang": "py",
            "retries": 0,
            "task": "tests.contrib.celery.test_integration.fn_task_parameters",
            "group": None,
            "timelimit": [None, None],
            "parent_id": None,
            "argsrepr": "['user']",
            "eta": None,
        },
        "properties": {
            "reply_to": "c3054a07-5b28-3855-b18c-1623a24aaeca",
            "correlation_id": test_id,
        },
    }

    task_id = retrieve_task_id(context)
    assert task_id == test_id


def test_task_id_from_blank_context():
    # if there is no context (thus missing headers and body),
    # no task_id is returned
    context = {}

    task_id = retrieve_task_id(context)
    assert task_id is None
