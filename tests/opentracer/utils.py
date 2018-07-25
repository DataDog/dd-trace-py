from tests.test_tracer import get_dummy_tracer


def opentracer_init(service_name="ot_svc", dummy_tracer=None):
    import opentracing
    from ddtrace.opentracer import Tracer  # , set_global_tracer
    from ddtrace.opentracer.tracer import set_global_tracer

    dummy_tracer = dummy_tracer or get_dummy_tracer()

    ot_tracer = Tracer(service_name)
    set_global_tracer(ot_tracer)

    ot_tracer._dd_tracer = dummy_tracer

    return ot_tracer, dummy_tracer
