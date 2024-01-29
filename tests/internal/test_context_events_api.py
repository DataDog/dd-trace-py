import threading
from time import sleep
from typing import Any
from typing import List
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
    def tearDown(self):
        core.reset_listeners()
        core._reset_context()

    def test_core_get_execution_context(self):
        context = core.ExecutionContext("foo")
        assert context.parents == []
        context.addParent(core.ExecutionContext("bar"))
        assert len(context.parents) == 1

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

    def test_core_dispatch_all_listeners(self):
        class Listener:
            calls: List[tuple]

            def __init__(self):
                self.calls = []

            def __call__(self, event_id: str, args: tuple) -> None:
                self.calls.append((event_id, args))

        l1 = Listener()

        core.event_hub.on_all(l1)

        core.dispatch("event.1", (1, 2))
        core.dispatch("event.2", ())

        with core.context_with_data("my.cool.context") as ctx:
            pass

        assert l1.calls == [
            ("event.1", (1, 2)),
            ("event.2", ()),
            ("context.started.my.cool.context", (ctx,)),
            ("context.started.start_span.my.cool.context", (ctx,)),
            ("context.ended.my.cool.context", (ctx,)),
        ]

    @with_config_raise_value(raise_value=False)
    def test_core_dispatch_exceptions_no_raise(self):
        def on_exception(*_):
            raise RuntimeError("OH NO!")

        def on_all_exception(*_):
            raise TypeError("OH NO!")

        core.on("my.cool.event", on_exception, "res")
        core.on("context.started.my.cool.context", on_exception)
        core.on("context.ended.my.cool.context", on_exception)
        core.event_hub.on_all(on_all_exception)

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
        def on_exception(*_):
            raise RuntimeError("OH NO!")

        def on_all_exception(*_):
            raise TypeError("OH NO!")

        core.on("my.cool.event", on_exception)
        core.on("context.started.my.cool.context", on_exception)
        core.on("context.ended.my.cool.context", on_exception)
        core.event_hub.on_all(on_all_exception)

        # We stop after the first exception is raised, on_all listeners get called first
        with pytest.raises(TypeError):
            core.dispatch("my.cool.event", (1, 2, 3))

        # We stop after the first exception is raised, on_all listeners get called first
        with pytest.raises(TypeError):
            core.dispatch_with_results("my.cool.event", (1, 2, 3))

        # We stop after the first exception is raised, on_all listeners get called first
        with pytest.raises(TypeError):
            with core.context_with_data("my.cool.context"):
                pass

    # The default raise value for tests is True, but let's be explicit to be safe
    @with_config_raise_value(raise_value=True)
    def test_core_dispatch_exceptions_raise(self):
        def on_exception(*_):
            raise RuntimeError("OH NO!")

        def noop(*_):
            pass

        core.on("my.cool.event", on_exception)
        core.on("context.started.my.cool.context", noop)
        core.on("context.ended.my.cool.context", on_exception)
        core.event_hub.on_all(noop)

        with pytest.raises(RuntimeError):
            core.dispatch("my.cool.event", (1, 2, 3))

        with pytest.raises(RuntimeError):
            core.dispatch_with_results("my.cool.event", (1, 2, 3))

        with pytest.raises(RuntimeError):
            with core.context_with_data("my.cool.context"):
                pass

    def test_core_dispatch_with_results_all_listeners(self):
        class Listener:
            calls: List[tuple]

            def __init__(self):
                self.calls = []

            def __call__(self, event_id: str, args: tuple) -> None:
                self.calls.append((event_id, args))

        l1 = Listener()

        core.event_hub.on_all(l1)

        # The results/exceptions from all listeners don't get reported
        assert core.dispatch_with_results("event.1", (1, 2)) is core.event_hub._MissingEventDict
        assert core.dispatch_with_results("event.2", ()) is core.event_hub._MissingEventDict

        with core.context_with_data("my.cool.context") as ctx:
            pass

        assert l1.calls == [
            ("event.1", (1, 2)),
            ("event.2", ()),
            ("context.started.my.cool.context", (ctx,)),
            ("context.started.start_span.my.cool.context", (ctx,)),
            ("context.ended.my.cool.context", (ctx,)),
        ]

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
        assert len(root_context.parents) == 0

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
            assert core.get_item(data_key) == data_value
        assert core.get_item(data_key) is None

    def test_core_set_item(self):
        data_key = "my.cool.data"
        data_value = "ban.ana2"
        with core.context_with_data("foobar"):
            assert core.get_item(data_key) is None
            core.set_item(data_key, data_value)
            assert core.get_item(data_key) == data_value
        assert core.get_item(data_key) is None

    def test_core_set_item_overwrite_attempt(self):
        data_key = "my.cool.data"
        data_value = "ban.ana2"
        with core.context_with_data("foobar", **{data_key: data_value}):
            with pytest.raises(ValueError):
                core.set_safe(data_key, "something else")
            assert core.get_item(data_key) == data_value
            core.set_item(data_key, "something else")
            assert core.get_item(data_key) == "something else"

    def test_core_context_relationship_across_threads(self):
        data_key = "banana"
        data_value = "bazinga"
        thread_nested_context_id = "in.nested"
        thread_context_id = "in.thread"

        def make_context(_results):
            with core.context_with_data(thread_context_id, **{data_key: data_value}):
                _results[thread_context_id] = dict()
                _results[thread_context_id][data_key] = core.get_item(data_key)
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
                assert core.get_item(data_key) == original_data_value

            new_data_value = "baz.inga"
            with core.context_with_data("foobaz", **{data_key: new_data_value}):
                assert core.get_item(data_key) == new_data_value
            assert core.get_item(data_key) == original_data_value


def test_core_context_data_concurrent_safety():
    data_key = "banana"
    other_context_started = threading.Event()
    set_result = threading.Event()

    def make_context(_results):
        with core.context_with_data("foo", **{data_key: "right"}):
            other_context_started.wait()
            _results[data_key] = core.get_item(data_key)
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
