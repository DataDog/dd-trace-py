import unittest

import mock

from ddtrace.internal import core


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
        assert False

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
        assert isinstance(core.root_context, core.ExecutionContext)
        assert len(core.root_context.parents) == 0
        assert len(core.root_context.children) == 0

    def test_core_current_context(self):
        assert core.current_context is core.root_context
        with core.context_with_data("foobar") as context:
            assert core.current_context is context
            assert context.parents[0] == core.root_context
        assert core.current_context is core.root_context

    def test_core_context_with_data(self):
        data_key = "my.cool.data"
        data_value = "ban.ana"
        with core.context_with_data("foobar", **{data_key: data_value}):
            assert core.get_item(data_key) == data_value
        assert core.get_item(data_key) is None

    def test_core_context_relationship_across_threads(self):
        assert False

    def test_core_context_with_data_inheritance(self):
        data_key = "my.cool.data"
        original_data_value = "ban.ana"
        with core.context_with_data("foobar", **{data_key: original_data_value}):
            with core.context_with_data("baz"):
                assert core.get_item(data_key) is None

            new_data_value = "baz.inga"
            with core.context_with_data("foobaz", **{data_key: new_data_value}):
                assert core.get_item(data_key) == new_data_value
            assert core.get_item(data_key) == original_data_value
