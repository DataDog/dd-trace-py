import pytest

from tests.utils import override_global_config


@pytest.fixture
def tracer_appsec(tracer):
    with override_global_config(dict(_appsec_enabled=True)):
        yield _enable_appsec(tracer)


def _enable_appsec(tracer):
    tracer._appsec_enabled = True
    # Hack: need to pass an argument to configure so that the processors are recreated
    tracer.configure(api_version="v0.4")
    return tracer
