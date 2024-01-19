from contextlib import contextmanager
from typing import Dict

import pytest

import ddtrace
from tests.utils import DummyTracer
from tests.utils import override_global_config


class Interface:
    def __init__(self, name, framework, client):
        self.name = name
        self.framework = framework
        self.client = client


class Contrib_TestClass_For_Threats:
    SERVER_PORT = 8000

    @pytest.fixture
    def interface(self) -> Interface:
        raise NotImplementedError

    def status(self, response) -> int:
        raise NotImplementedError

    def headers(self, response) -> Dict[str, str]:
        raise NotImplementedError

    def body(self, response) -> str:
        raise NotImplementedError

    def test_healthcheck(self, interface: Interface, root_span):
        # if interface.name == "fastapi":
        #    raise pytest.skip("fastapi does not have a healthcheck endpoint")
        with override_global_config(dict(_asm_enabled=True)):
            response = interface.client.get("/")
            assert self.status(response) == 200, "healthcheck failed"
            assert self.body(response) == "ok ASM"
            from ddtrace.settings.asm import config as asm_config

            assert asm_config._asm_enabled
            assert root_span().get_tag("http.status_code") == "200"
            assert self.headers(response)["content-type"] == "text/html; charset=utf-8"


@contextmanager
def test_tracer():
    tracer = DummyTracer()
    original_tracer = ddtrace.tracer
    ddtrace.tracer = tracer

    # Yield to our test
    tracer.configure(api_version="v0.4")
    yield tracer
    tracer.pop()
    ddtrace.tracer = original_tracer


@contextmanager
def post_tracer(interface):
    original_tracer = ddtrace.Pin.get_from(interface.framework).tracer
    ddtrace.Pin.override(interface.framework, tracer=interface.tracer)
    yield
    ddtrace.Pin.override(interface.framework, tracer=original_tracer)
