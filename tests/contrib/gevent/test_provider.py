from ddtrace.contrib.gevent import provider


def test_call_hooks(tracer):
    data = {}

    def record(context):
        if "trace" not in data:
            data["trace"] = context

    tracer.configure(context_provider=provider.GeventContextProvider())
    tracer.context_provider._on_activate(record)
    with tracer.trace("test123") as mytrace:
        pass
    assert data["trace"] is mytrace
