from ddtrace import _hooks


def test_deregister():
    hooks = _hooks.Hooks()

    x = {}

    @hooks.register("key")
    def do_not_call():
        x["x"] = True

    hooks.emit("key")
    assert x["x"]
    del x["x"]

    hooks.deregister("key", do_not_call)

    hooks.emit("key")

    assert len(x) == 0


def test_deregister_unknown():
    hooks = _hooks.Hooks()

    hooks.deregister("key", test_deregister_unknown)

    hooks.emit("key")
