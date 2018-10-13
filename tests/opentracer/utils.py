import pytest

from ddtrace.opentracer import Tracer, set_global_tracer

from tests.test_tracer import get_dummy_tracer


@pytest.fixture()
def ot_tracer_factory():
    """Fixture which returns an opentracer ready to use for testing."""

    def make_ot_tracer(
        service_name="my_svc", config=None, scope_manager=None, context_provider=None
    ):
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

    return make_ot_tracer


@pytest.fixture()
def ot_tracer(ot_tracer_factory):
    """Fixture for a default opentracer."""
    return ot_tracer_factory()


@pytest.fixture()
def global_tracer(ot_tracer):
    """A function similar to one OpenTracing users would write to initialize
    their OpenTracing tracer.
    """
    set_global_tracer(ot_tracer)

    return ot_tracer


@pytest.fixture()
def writer(ot_tracer):
    return ot_tracer._dd_tracer.writer


@pytest.fixture()
def dd_tracer(ot_tracer):
    return ot_tracer._dd_tracer


def init_tracer(service_name, dd_tracer, scope_manager=None):
    """A method that emulates what a user of OpenTracing would call to
    initialize a Datadog opentracer.

    It accepts a Datadog tracer that should be the same one used for testing.
    """
    writer = dd_tracer.writer
    ot_tracer = Tracer(service_name, dd_tracer=dd_tracer, scope_manager=scope_manager)
    dd_tracer.writer = writer
    ot_tracer._dd_tracer = dd_tracer
    return ot_tracer
