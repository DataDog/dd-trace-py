from ddtrace.internal import core


def test_core_get_execution_context():
    context = core.ExecutionContext()
    assert context.parents == []
    assert context.children == []
    context.addParent(core.ExecutionContext())
    context.addChild(core.ExecutionContext())
    assert len(context.parents) == 1
    assert len(context.children) == 1


def test_core_has_listeners():
    event_name = "my.cool.event"
    has_listeners = core.has_listeners(event_name)
    assert not has_listeners
    core.on(event_name, lambda *args: print("banana"))
    has_listeners = core.has_listeners(event_name)
    assert has_listeners


def test_core_root_context():
    assert isinstance(core.root_context, core.ExecutionContext)
    assert len(core.root_context.parents) == 0
    assert len(core.root_context.children) == 0


def test_core_current_context():
    assert core.current_context is core.root_context
    with core.context_with_data() as context:
        assert core.current_context is context
        assert context.parents[0] == core.root_context
    assert core.current_context is core.root_context


def test_core_context_with_data():
    data_key = "my.cool.data"
    data_value = "ban.ana"
    with core.context_with_data(**{data_key: data_value}):
        assert core.get_item(data_key) == data_value


def test_core_context_with_data_inheritance():
    data_key = "my.cool.data"
    original_data_value = "ban.ana"
    with core.context_with_data(**{data_key: original_data_value}):
        with core.context_with_data():
            assert core.get_item(data_key) == original_data_value

        new_data_value = "baz.inga"
        with core.context_with_data(**{data_key: new_data_value}):
            assert core.get_item(data_key) == new_data_value
        assert core.get_item(data_key) == original_data_value


def test_core_get_item():
    pass


def test_core_on():
    pass


def test_core_dispatch():
    pass
