import threading
from time import sleep
import unittest

import mock
import pytest

from ddtrace.internal import core


class TestContextEventsApi(unittest.TestCase):
    def tearDown(self):
        core.reset_listeners()

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

    def test_core_dispatch(self):
        event_name = "my.cool.event"
        dynamic_value = 42
        handler_return = "from.event.{}"
        core.on(event_name, lambda magic_number: handler_return.format(magic_number))
        result = core.dispatch(event_name, [dynamic_value])[0][0]
        assert result == handler_return.format(dynamic_value)

    def test_core_dispatch_star_args(self):
        event_name = "my.cool.event"
        dynamic_value = 42
        handler_return = "from.event.{}"
        core.on(event_name, lambda magic_number, forty_two: handler_return.format(magic_number))
        result = core.dispatch(event_name, dynamic_value, 42)[0][0]
        assert result == handler_return.format(dynamic_value)

    def test_core_dispatch_multiple_listeners(self):
        event_name = "my.cool.event"
        dynamic_value = 42
        handler_return = "from.event.{}"
        core.on(event_name, lambda magic_number: handler_return.format(magic_number))
        core.on(event_name, lambda another_magic_number: handler_return + str(another_magic_number) + "!")
        results, _ = core.dispatch(event_name, [dynamic_value])
        assert results[0] == handler_return.format(dynamic_value)
        assert results[1] == handler_return + str(dynamic_value) + "!"

    def test_core_dispatch_multiple_listeners_multiple_threads(self):
        event_name = "my.cool.event"

        def make_target(make_target_id):
            def target():
                def listener():
                    if make_target_id % 2 == 0:
                        return make_target_id * 2
                    else:
                        raise ValueError

                core.on(event_name, listener)

            sleep(make_target_id * 0.0001)  # ensure threads finish in order
            return target

        threads = []
        thread_count = 10
        for idx in range(thread_count):
            t = threading.Thread(target=make_target(idx))
            t.start()
            threads.append(t)

        results, exceptions = core.dispatch(event_name, [])

        for t in threads:
            t.join()

        results = [r for r in results if r is not None]
        expected = list(i * 2 for i in range(thread_count) if i % 2 == 0)
        assert sorted(results) == sorted(expected)
        assert len(exceptions) <= thread_count
        for idx, exception in enumerate(exceptions):
            if idx % 2 == 0:
                assert exception is None
            else:
                assert isinstance(exception, ValueError)

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
