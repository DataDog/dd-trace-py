import threading
from time import sleep
from typing import Any
import unittest

import mock
import pytest

from ddtrace import config
from ddtrace.internal import core


def with_config_raise_value(raise_value: bool):
    def _outer(func):
        def _inner(*args, **kwargs):
            original_value = config._raise
            config._raise = raise_value
            try:
                return func(*args, **kwargs)
            finally:
                config._raise = original_value

        return _inner

    return _outer


class TestContextEventsApi(unittest.TestCase):
    def setUp(self):
        core.reset_listeners()
        core._reset_context()

    def tearDown(self):
        core.reset_listeners()
        core._reset_context()

    def test_core_get_execution_context(self):
        context = core.ExecutionContext("foo")
        assert context.parent is None
        context.parent = core.ExecutionContext("bar")
        assert context.parent is not None

    def test_core_has_listeners(self):
        event_name = "my.cool.event"
        has_listeners = core.has_listeners(event_name)
        assert not has_listeners
        core.on(event_name, lambda *args: True)
        has_listeners = core.has_listeners(event_name)
        assert has_listeners

    def test_core_dispatch_with_results(self):
        event_name = "my.cool.event"
        dynamic_value = 42
        handler_return = "from.event.{}"
        core.on(event_name, lambda magic_number: handler_return.format(magic_number), "res")
        result = core.dispatch_with_results(event_name, (dynamic_value,)).res.value
        assert result == handler_return.format(dynamic_value)

    def test_core_dispatch_with_results_multiple_args(self):
        event_name = "my.cool.event"
        dynamic_value = 42
        handler_return = "from.event.{}"
        core.on(event_name, lambda magic_number, forty_two: handler_return.format(magic_number), "res")
        result = core.dispatch_with_results(event_name, (dynamic_value, 42)).res.value
        assert result == handler_return.format(dynamic_value)

    def test_core_dispatch_with_results_multiple_listeners(self):
        event_name = "my.cool.event"
        dynamic_value = 42
        handler_return = "from.event.{}"
        core.on(event_name, lambda magic_number: handler_return.format(magic_number), "res_a")
        core.on(event_name, lambda another_magic_number: handler_return + str(another_magic_number) + "!", "res_b")
        results = core.dispatch_with_results(event_name, (dynamic_value,))
        assert results.res_a.value == handler_return.format(dynamic_value)
        assert results.res_b.value == handler_return + str(dynamic_value) + "!"

    def test_core_dispatch_with_results_multiple_listeners_multiple_threads(self):
        event_name = "my.cool.event"

        def make_target(make_target_id):
            def target():
                def listener():
                    if make_target_id % 2 == 0:
                        return make_target_id * 2

                core.on(event_name, listener, f"name_{make_target_id}")

            sleep(make_target_id * 0.0001)  # ensure threads finish in order
            return target

        threads = []
        thread_count = 10
        for idx in range(thread_count):
            t = threading.Thread(target=make_target(idx))
            t.start()
            threads.append(t)

        result = core.dispatch_with_results(event_name, ())

        for t in threads:
            t.join()

        results = [r.value for r in result.values() if r.value is not None]
        exceptions = [r.exception for r in result.values() if r.exception is not None]
        expected = list(i * 2 for i in range(thread_count) if i % 2 == 0)
        assert sorted(results) == sorted(expected)
        assert len(exceptions) <= thread_count

    def test_core_dispatch(self):
        class Listener:
            args: tuple

            def __call__(self, *args: Any) -> None:
                self.args = args

        listener = Listener()

        event_name = "my.cool.event"
        dynamic_value = 42
        core.on(event_name, listener)
        core.dispatch(event_name, (dynamic_value,))

        assert listener.args == (dynamic_value,)

    def test_core_dispatch_multiple_args(self):
        class Listener:
            results: int = 0

            def __call__(self, magic_number: int, forty_two: int) -> None:
                self.results += magic_number + forty_two

        l1 = Listener()

        event_name = "my.cool.event"
        dynamic_value = 42
        core.on(event_name, l1)
        core.dispatch(event_name, (dynamic_value, 42))
        assert l1.results == 84

    def test_core_dispatch_multiple_listeners(self):
        class Listener:
            results: int = 0

            def __call__(self, magic_number: int) -> None:
                self.results += magic_number

        l1 = Listener()
        l2 = Listener()

        event_name = "my.cool.event"
        dynamic_value = 42
        core.on(event_name, l1)
        core.on(event_name, l2)
        core.dispatch(event_name, (dynamic_value,))

        assert l1.results == dynamic_value
        assert l2.results == dynamic_value

    def test_core_dispatch_multiple_listeners_multiple_threads(self):
        event_name = "my.cool.event"

        class Listener:
            calls: int = 0

            def __call__(self) -> None:
                self.calls += 1

        def make_target(make_target_id, listener):
            def target():
                core.on(event_name, listener)

            sleep(make_target_id * 0.0001)  # ensure threads finish in order
            return target

        threads = []
        thread_count = 10
        listeners = [Listener()] * thread_count
        for idx in range(thread_count):
            t = threading.Thread(target=make_target(idx, listeners[idx]))
            t.start()
            threads.append(t)

        core.dispatch(event_name, ())

        for t in threads:
            t.join()

        for listener in listeners:
            assert listener.calls == 1

    @with_config_raise_value(raise_value=False)
    def test_core_dispatch_exceptions_no_raise(self):
        def on_exception(*_):
            raise RuntimeError("OH NO!")

        core.on("my.cool.event", on_exception, "res")
        core.on("context.started.my.cool.context", on_exception)
        core.on("context.ended.my.cool.context", on_exception)

        # Dispatch does not raise any exceptions, and returns nothing
        assert core.dispatch("my.cool.event", (1, 2, 3)) is None

        # Dispatch with results will return the exception from the on listener only
        result = core.dispatch_with_results("my.cool.event", (1, 2, 3)).res
        assert result.value is None
        assert isinstance(result.exception, RuntimeError)
        assert result.response_type == core.event_hub.ResultType.RESULT_EXCEPTION

        # Context with data continues to work as expected
        with core.context_with_data("my.cool.context"):
            pass

    # The default raise value for tests is True, but let's be explicit to be safe
    @with_config_raise_value(raise_value=True)
    def test_core_dispatch_exceptions_all_raise(self):
        def on_runtime_error(*_):
            raise RuntimeError("OH NO!")

        def on_type_error(*_):
            raise TypeError("OH NO!")

        # Register 2 listeners for 1 event, the first one gets called first
        core.on("my.cool.event", on_runtime_error)
        core.on("my.cool.event", on_type_error)

        core.on("context.started.my.cool.context", on_type_error)
        core.on("context.ended.my.cool.context", on_runtime_error)

        # We stop after the first exception is raised, on_runtime_error listeners get called first
        with pytest.raises(RuntimeError):
            core.dispatch("my.cool.event", (1, 2, 3))

        # We stop after the first exception is raised, on_runtime_error listeners get called first
        with pytest.raises(RuntimeError):
            core.dispatch_with_results("my.cool.event", (1, 2, 3))

        # We stop after the first exception raised, which is the context started event
        with pytest.raises(TypeError):
            with core.context_with_data("my.cool.context"):
                pass

    def test_core_dispatch_args_list_coerced_to_tuple(self):
        """dispatch gracefully accepts a list in place of a tuple."""
        received = []

        def listener(a, b):
            received.append((a, b))

        core.on("my.cool.event", listener)
        core.dispatch("my.cool.event", [1, 2])
        assert received == [(1, 2)]

    def test_core_dispatch_args_invalid_type_does_not_raise(self):
        """dispatch with a non-iterable args value calls listener with no args and does not crash."""
        called = []

        def listener():
            called.append(True)

        core.on("my.cool.event", listener)
        # An integer is not iterable — coerce_to_tuple falls back to empty tuple.
        core.dispatch("my.cool.event", 42)  # type: ignore[arg-type]
        assert called == [True]

    def test_core_dispatch_with_results_args_list_coerced_to_tuple(self):
        """dispatch_with_results gracefully accepts a list in place of a tuple."""
        core.on("my.cool.event", lambda a, b: a + b, "res")
        results = core.dispatch_with_results("my.cool.event", [3, 4])
        assert results.res.value == 7

    @with_config_raise_value(raise_value=False)
    def test_core_dispatch_allow_raise_propagates_exception(self):
        def on_runtime_error(*_):
            raise RuntimeError("OH NO!")

        core.on("my.cool.event", on_runtime_error)

        # Default allow_raise=False: Exception is swallowed
        assert core.dispatch("my.cool.event", (1,)) is None

        # allow_raise=True: Exception propagates
        with pytest.raises(RuntimeError):
            core.dispatch("my.cool.event", (1,), allow_raise=True)

    @with_config_raise_value(raise_value=False)
    def test_core_dispatch_allow_raise_short_circuits_listeners(self):
        calls = []

        def first(*_):
            calls.append("first")
            raise RuntimeError("first failed")

        def second(*_):
            calls.append("second")

        core.on("my.cool.event", first, "first")
        core.on("my.cool.event", second, "second")

        with pytest.raises(RuntimeError):
            core.dispatch("my.cool.event", (), allow_raise=True)
        assert calls == ["first"]

    @with_config_raise_value(raise_value=False)
    def test_core_dispatch_ddblockexception_always_propagates(self):
        from ddtrace.internal._exceptions import DDBlockException

        class FakeBlock(DDBlockException):
            pass

        def on_block(*_):
            raise FakeBlock()

        core.on("my.cool.event", on_block)

        # allow_raise=False (default): BaseException-derived block still propagates
        # (it's not caught by the internal `except Exception:`)
        with pytest.raises(DDBlockException):
            core.dispatch("my.cool.event", ())

        # allow_raise=True: also propagates
        with pytest.raises(DDBlockException):
            core.dispatch("my.cool.event", (), allow_raise=True)

    @with_config_raise_value(raise_value=False)
    def test_core_dispatch_event_allow_raise_propagates(self):
        from ddtrace.internal.core.event_hub import dispatch_event

        class FakeEvent:
            event_name = "my.cool.event"

        def listener(_):
            raise RuntimeError("evt boom")

        core.on("my.cool.event", listener)

        # Default allow_raise=False: swallowed
        assert dispatch_event(FakeEvent()) is None

        # allow_raise=True: propagates
        with pytest.raises(RuntimeError):
            dispatch_event(FakeEvent(), allow_raise=True)

    def test_ddblockexception_inheritance(self):
        from ddtrace.internal._exceptions import BlockingException
        from ddtrace.internal._exceptions import DDBlockException

        # DDBlockException is BaseException-derived only, not Exception
        assert issubclass(DDBlockException, BaseException)
        assert not issubclass(DDBlockException, Exception)

        # BlockingException inherits the new base class
        assert issubclass(BlockingException, DDBlockException)

        # AIGuardAbortError inherits from DDBlockException
        from ddtrace.appsec.ai_guard._api_client import AIGuardAbortError

        assert issubclass(AIGuardAbortError, DDBlockException)
        assert not issubclass(AIGuardAbortError, Exception)

    def test_core_dispatch_context_ended(self):
        context_id = "my.cool.context"
        event_name = "context.ended.%s" % context_id
        handler = mock.Mock()
        core.on(event_name, handler)
        assert not handler.called
        with core.context_with_data(context_id):
            pass
        assert handler.called

    def test_core_root_context(self):
        root_context = core._CURRENT_CONTEXT.get()
        assert isinstance(root_context, core.ExecutionContext)
        assert root_context.parent is None

    def test_core_current_context(self):
        assert core._CURRENT_CONTEXT.get().identifier == core.ROOT_CONTEXT_ID
        with core.context_with_data("foobar") as context:
            assert core._CURRENT_CONTEXT.get() is context
            assert context.parent.identifier == core.ROOT_CONTEXT_ID
        assert core._CURRENT_CONTEXT.get().identifier == core.ROOT_CONTEXT_ID

    def test_core_context_with_data(self):
        data_key = "my.cool.data"
        data_value = "ban.ana"
        with core.context_with_data("foobar", **{data_key: data_value}):
            assert core.find_item(data_key) == data_value
        assert core.find_item(data_key) is None

    def test_core_set_item(self):
        data_key = "my.cool.data"
        data_value = "ban.ana2"
        with core.context_with_data("foobar"):
            assert core.find_item(data_key) is None
            core.set_item(data_key, data_value)
            assert core.find_item(data_key) == data_value
        assert core.find_item(data_key) is None

    def test_core_set_item_overwrite_attempt(self):
        data_key = "my.cool.data"
        data_value = "ban.ana2"
        with core.context_with_data("foobar", **{data_key: data_value}):
            with pytest.raises(ValueError):
                core.set_safe(data_key, "something else")
            assert core.find_item(data_key) == data_value
            core.set_item(data_key, "something else")
            assert core.find_item(data_key) == "something else"

    def test_core_context_relationship_across_threads(self):
        data_key = "banana"
        data_value = "bazinga"
        thread_nested_context_id = "in.nested"
        thread_context_id = "in.thread"

        def make_context(_results):
            with core.context_with_data(thread_context_id, **{data_key: data_value}):
                _results[thread_context_id] = dict()
                _results[thread_context_id][data_key] = core.find_item(data_key)
                _results[thread_context_id]["_id"] = core._CURRENT_CONTEXT.get().identifier
                _results[thread_context_id]["parent"] = core._CURRENT_CONTEXT.get().parent.identifier
                with core.context_with_data(thread_nested_context_id):
                    _results[thread_nested_context_id] = dict()
                    _results[thread_nested_context_id]["_id"] = core._CURRENT_CONTEXT.get().identifier
                    _results[thread_nested_context_id]["parent"] = core._CURRENT_CONTEXT.get().parent.identifier

        results = dict()

        with core.context_with_data("main"):
            thread_that_makes_context = threading.Thread(target=make_context, args=(results,))
            thread_that_makes_context.start()
            thread_that_makes_context.join()

        assert results[thread_context_id]["_id"] == thread_context_id
        assert results[thread_context_id]["parent"] == core.ROOT_CONTEXT_ID
        assert results[thread_context_id][data_key] == data_value
        assert results[thread_nested_context_id]["_id"] == thread_nested_context_id
        assert results[thread_nested_context_id]["parent"] == thread_context_id

    def test_core_context_with_data_inheritance(self):
        data_key = "my.cool.data"
        original_data_value = "ban.ana"
        with core.context_with_data("foobar", **{data_key: original_data_value}):
            with core.context_with_data("baz"):
                assert core.find_item(data_key) == original_data_value

            new_data_value = "baz.inga"
            with core.context_with_data("foobaz", **{data_key: new_data_value}):
                assert core.find_item(data_key) == new_data_value
            assert core.find_item(data_key) == original_data_value


def test_core_context_data_concurrent_safety():
    data_key = "banana"
    other_context_started = threading.Event()
    set_result = threading.Event()

    def make_context(_results):
        with core.context_with_data("foo", **{data_key: "right"}):
            other_context_started.wait()
            _results[data_key] = core.find_item(data_key)
            set_result.set()

    def make_another_context():
        with core.context_with_data("bar", **{data_key: "wrong"}):
            other_context_started.set()
            set_result.wait()

    results = dict()

    task1 = threading.Thread(target=make_context, args=(results,))
    task2 = threading.Thread(target=make_another_context)

    task1.start()
    task2.start()
    task1.join()
    task2.join()

    assert results[data_key] == "right"


@pytest.mark.parametrize("nb_threads", [2, 16, 256])
def test_core_in_threads(nb_threads):
    """Test nested contexts in multiple threads with set/get/discard and global values in main context."""
    import asyncio
    import random

    witness = object()

    async def get_set_isolated(value: str):
        with core.context_with_data(value):
            core.set_item("key", value)
            with core.context_with_data(value):
                v = f"in {value}"
                core.set_item("key", v)
                await asyncio.sleep(random.random())
                assert core.find_item("key") == v
                core.discard_item("key")
                assert core.get_item("key") is None
                assert core.find_item("key") == value
                core.find_item("global_counter")["value"] += 1
            assert core.find_item("key") == value
            core.discard_item("key")
            assert core.find_item("key") is None
            return witness

    async def create_tasks_func():
        tasks = [loop.create_task(get_set_isolated(str(i))) for i in range(nb_threads)]
        await asyncio.wait(tasks)
        return [task.result() for task in tasks]

    with core.context_with_data("main"):
        core.set_item("global_counter", {"value": 0})
        loop = asyncio.new_event_loop()
        assert not loop.is_closed()
        res = loop.run_until_complete(create_tasks_func())
        assert isinstance(res, list)
        assert all(s is witness for s in res)
        loop.close()
        assert core.find_item("global_counter")["value"] == nb_threads


# ── event hub unit tests ──────────────────────────────────────────────────────


@pytest.fixture(autouse=False)
def clean_event_hub():
    core.reset_listeners()
    yield
    core.reset_listeners()


def test_event_result_bool(clean_event_hub):
    core.on("test.event", lambda: 42, "ok_res")
    results = core.dispatch_with_results("test.event", ())
    assert bool(results.ok_res) is True
    assert bool(results.nonexistent_key) is False


def test_event_result_dict_missing_key_returns_sentinel(clean_event_hub):
    core.on("test.event", lambda: 1, "res")
    results = core.dispatch_with_results("test.event", ())
    missing = results["no_such_key"]
    assert missing.response_type == core.event_hub.ResultType.RESULT_UNDEFINED
    assert missing.value is None
    assert bool(missing) is False


def test_dispatch_with_results_no_listeners_returns_empty_dict(clean_event_hub):
    results = core.dispatch_with_results("test.event.noop", ())
    assert len(results) == 0
    missing = results["anything"]
    assert missing.response_type == core.event_hub.ResultType.RESULT_UNDEFINED


def test_on_name_deduplication(clean_event_hub):
    calls = []
    core.on("test.event", lambda: calls.append("first"), "my_listener")
    core.on("test.event", lambda: calls.append("second"), "my_listener")
    core.dispatch("test.event", ())
    assert calls == ["second"]


def test_reset_listeners_removes_bound_method(clean_event_hub):
    """reset_listeners must remove a bound method even though Python creates a new
    object on every attribute access (a.method is not a.method).  Using pointer
    identity instead of Python equality silently no-ops the reset, causing
    listeners to accumulate across disable/enable cycles.
    """

    class Listener:
        def __init__(self):
            self.calls = []

        def handle(self):
            self.calls.append(True)

    obj = Listener()

    # Register via one bound-method object, reset via a different one — same
    # logical method, different Python objects.
    core.on("test.event", obj.handle)
    assert core.has_listeners("test.event")

    core.reset_listeners("test.event", obj.handle)  # different object, same method
    assert not core.has_listeners("test.event")

    core.dispatch("test.event", ())
    assert obj.calls == [], "listener was not removed; reset used pointer identity"


def test_dispatch_base_exception_propagates_regardless_of_raise_flag(clean_event_hub):
    """BaseException subclasses must propagate even when config._raise is False.
    Dispatch uses `except Exception:` semantics — BaseException is never swallowed."""

    class BlockingException(BaseException):
        pass

    def listener():
        raise BlockingException("blocked!")

    core.on("test.event", listener)
    original = config._raise
    config._raise = False
    try:
        with pytest.raises(BlockingException, match="blocked!"):
            core.dispatch("test.event", ())
    finally:
        config._raise = original


def test_dispatch_with_results_base_exception_propagates_regardless_of_raise_flag(clean_event_hub):
    """BaseException subclasses must propagate from dispatch_with_results even when
    config._raise is False — they are never stored as EventResult.exception."""

    class BlockingException(BaseException):
        pass

    def listener():
        raise BlockingException("blocked!")

    core.on("test.event", listener, "res")
    original = config._raise
    config._raise = False
    try:
        with pytest.raises(BlockingException, match="blocked!"):
            core.dispatch_with_results("test.event", ())
    finally:
        config._raise = original


def test_reset_listeners_simulate_disable_enable_cycle(clean_event_hub):
    """Simulate what LLMObs does on every test: disable() → reset_listeners(),
    enable() → on().  If reset silently fails, listeners accumulate and fire
    multiple times, corrupting any state that depends on exactly one call.
    """

    class Component:
        def __init__(self, tag):
            self.calls = []
            self.tag = tag

        def on_event(self):
            self.calls.append(self.tag)

    c1 = Component("first")
    c2 = Component("second")

    # Cycle 1: register c1, then "disable" (reset), then "enable" c2
    core.on("test.event", c1.on_event)
    core.reset_listeners("test.event", c1.on_event)
    core.on("test.event", c2.on_event)

    core.dispatch("test.event", ())

    assert c1.calls == [], "stale listener from cycle 1 still firing"
    assert c2.calls == ["second"]
