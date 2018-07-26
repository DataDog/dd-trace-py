from ddtrace.opentracer import Tracer, set_global_tracer

from tests.test_tracer import get_dummy_tracer


def get_dummy_ot_tracer(
    service_name="", config=None, scope_manager=None, context_provider=None
):
    """Returns a dummy tracer to use for testing.

    This is similar to the dummy tracer usage in place for the datadog tracer.
    """
    config = config or {}
    tracer = Tracer(
        service_name=service_name, config=config, scope_manager=scope_manager
    )

    # similar to how we test the ddtracer, use a dummy tracer
    dd_tracer = get_dummy_tracer()
    if context_provider:
        dd_tracer.configure(context_provider=context_provider)

    # attach the dummy tracer to the opentracer
    tracer._dd_tracer = dd_tracer
    return tracer


def opentracer_init(service_name="ot_svc", set_global=True):
    """A function similar to one OpenTracing users would write to initialize
    their OpenTracing tracer.
    """

    ot_tracer = get_dummy_ot_tracer(service_name)
    dd_tracer = ot_tracer._dd_tracer

    if set_global:
        set_global_tracer(ot_tracer)

    return ot_tracer, dd_tracer
