from tests.test_tracer import get_dummy_tracer


def opentracer_init(service_name="ot_svc", set_global=True, dummy_tracer=None):
    """A function similar to one OpenTracing users would write to initialize
    their OpenTracing tracer.
    """
    from ddtrace.opentracer import Tracer, set_global_tracer

    dummy_tracer = dummy_tracer or get_dummy_tracer()

    ot_tracer = Tracer(service_name)
    ot_tracer._dd_tracer = dummy_tracer

    if set_global:
        set_global_tracer(ot_tracer)

    return ot_tracer, dummy_tracer
