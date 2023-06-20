import threading
import time
from typing import Dict
import unittest

import mock
import pytest

from ddtrace.internal import core
from ddtrace.internal.compat import PY3


pytest_plugins = ("pytest_asyncio",)


class TestContextEventsApi(unittest.TestCase):
    def setUp(self):
        core._event_hub = core.EventHub()

    def test_core_get_execution_context(self):
        context = core.ExecutionContext("foo")
        assert context.parents == []
        assert context.children == []
        context.addParent(core.ExecutionContext("bar"))
        context.addChild(core.ExecutionContext("baz"))
        assert len(context.parents) == 1
        assert len(context.children) == 1

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

        assert results == list((i * 2) if i % 2 == 0 else None for i in range(thread_count))
        assert len(exceptions) == thread_count
        for idx, exception in enumerate(exceptions):
            if idx % 2 == 0:
                assert exception is None
            else:
                assert isinstance(exception, ValueError)

    def test_core_concurrent_dispatch(self):
        event_name = "my.cool.event"

        def dispatcher(_results: Dict):
            _results["results"] = core.dispatch(event_name, [])[0]

        def make_listener(delay: float):
            def listener():
                def _listen():
                    time.sleep(delay)
                    return 42

                core.on(event_name, _listen)

            return listener

        dispatcher_results = dict()

        dispatcher_thread = threading.Thread(target=dispatcher, args=(dispatcher_results,))
        listener_thread_slow = threading.Thread(target=make_listener(1))
        listener_thread_fast = threading.Thread(target=make_listener(0))

        listener_thread_slow.start()
        dispatcher_thread.start()
        listener_thread_fast.start()

        listener_thread_fast.join()
        dispatcher_thread.join()
        listener_thread_slow.join()

        assert (
            len(dispatcher_results["results"]) == 1
        ), "Listeners should not be triggered by dispatch() calls concurrent with their on() registration"
        assert dispatcher_results["results"][0] == 42

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
        assert len(root_context.children) == 0

    def test_core_current_context(self):
        assert core._CURRENT_CONTEXT.get().identifier == "root"
        with core.context_with_data("foobar") as context:
            assert core._CURRENT_CONTEXT.get() is context
            assert context.parent.identifier == "root"
        assert core._CURRENT_CONTEXT.get().identifier == "root"

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
        assert results[thread_context_id]["parent"] == "root"
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


if PY3:

    @pytest.mark.asyncio
    async def test_core_context_data_concurrent_safety():
        import asyncio

        data_key = "banana"
        other_context_started = asyncio.Event()
        set_result = asyncio.Event()

        async def make_context(_results):
            with core.context_with_data("foo", **{data_key: "right"}):
                await other_context_started.wait()
                # XXX if another async task creates a new context here, get_item on the next line
                # might see that context's data rather than its task-local data
                # adjust this test to catch this case
                _results[data_key] = core.get_item(data_key)
                set_result.set()

        async def make_another_context():
            with core.context_with_data("bar", **{data_key: "wrong"}):
                other_context_started.set()
                await set_result.wait()

        results = dict()

        task1 = asyncio.create_task(make_context(results))
        task2 = asyncio.create_task(make_another_context())

        await task1
        await task2

        assert results[data_key] == "right"
