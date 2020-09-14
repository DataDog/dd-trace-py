import pytest

from ddtrace import Pin, Tracer
from ddtrace.settings import Config
from ddtrace.contrib import trace_utils

from tests import override_global_config


@pytest.fixture
def config():
    c = Config()
    c._add("myint", dict())
    return c


@pytest.mark.parametrize(
    "pin,config_val,default,global_service,expected",
    [
        (Pin(), None, None, None, None),
        (Pin(), None, None, "global-svc", "global-svc"),
        (Pin(), None, "default-svc", None, "default-svc"),
        # Global service should have higher priority than the integration default.
        (Pin(), None, "default-svc", "global-svc", "global-svc"),
        (Pin(), "config-svc", "default-svc", None, "config-svc"),
        (Pin(service="pin-svc"), None, "default-svc", None, "pin-svc"),
        (Pin(service="pin-svc"), "config-svc", "default-svc", None, "pin-svc"),
    ],
)
def test_int_service(config, pin, config_val, default, global_service, expected):
    if config_val:
        config.myint.service = config_val

    if global_service:
        config.service = global_service

    assert trace_utils.int_service(pin, config.myint, default) == expected


def test_int_service_integration(config):
    pin = Pin()
    tracer = Tracer()
    assert trace_utils.int_service(pin, config.myint) is None

    with override_global_config(dict(service="global-svc")):
        assert trace_utils.int_service(pin, config.myint) is None

        with tracer.trace("something", service=trace_utils.int_service(pin, config.myint)) as s:
            assert s.service == "global-svc"


@pytest.mark.parametrize(
    "pin,config_val,default,expected",
    [
        (Pin(), None, "default-svc", "default-svc"),
        (Pin(), "config-svc", "default-svc", "config-svc"),
        (Pin(service="pin-svc"), None, "default-svc", "pin-svc"),
        (Pin(service="pin-svc"), "config-svc", "default-svc", "pin-svc"),
    ],
)
def test_ext_service(config, pin, config_val, default, expected):
    if config_val:
        config.myint.service = config_val

    assert trace_utils.ext_service(pin, config.myint, default) == expected
