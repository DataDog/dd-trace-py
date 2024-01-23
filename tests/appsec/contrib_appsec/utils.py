from contextlib import contextmanager
import json
from typing import Dict
from urllib.parse import urlencode

import pytest

import ddtrace
from ddtrace.appsec._constants import APPSEC
from ddtrace.internal import core
from ddtrace.settings.asm import config as asm_config
import tests.appsec.rules as rules
from tests.utils import DummyTracer
from tests.utils import override_env
from tests.utils import override_global_config


class Interface:
    def __init__(self, name, framework, client):
        self.name = name
        self.framework = framework
        self.client = client


def payload_to_xml(payload: Dict[str, str]) -> str:
    return "".join(f"<{k}>{v}</{k}>" for k, v in payload.items())


def payload_to_plain_text(payload: Dict[str, str]) -> str:
    return "\n".join(f"{k}={v}" for k, v in payload.items())


class Contrib_TestClass_For_Threats:
    """
    Factorized test class for threats tests on all supported frameworks
    """

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

    def update_tracer(self, interface):
        interface.tracer._asm_enabled = asm_config._asm_enabled
        interface.tracer._iast_enabled = asm_config._iast_enabled
        interface.tracer.configure(api_version="v0.4")

    @pytest.mark.parametrize("asm_enabled", [True, False])
    def test_healthcheck(self, interface: Interface, get_tag, asm_enabled: bool):
        # you can disable any test in a framework like that:
        # if interface.name == "fastapi":
        #    raise pytest.skip("fastapi does not have a healthcheck endpoint")
        with override_global_config(dict(_asm_enabled=asm_enabled)):
            response = interface.client.get("/")
            assert self.status(response) == 200, "healthcheck failed"
            assert self.body(response) == "ok ASM"
            from ddtrace.settings.asm import config as asm_config

            assert asm_config._asm_enabled is asm_enabled
            assert get_tag("http.status_code") == "200"
            assert self.headers(response)["content-type"] == "text/html; charset=utf-8"

    def test_simple_attack(self, interface: Interface, root_span, get_tag):
        with override_global_config(dict(_asm_enabled=True)):
            self.update_tracer(interface)
            response = interface.client.get("/.git?q=1")
            assert response.status_code == 404
            str_json = get_tag(APPSEC.JSON)
            assert str_json is not None, "no JSON tag in root span"
            assert "triggers" in json.loads(str_json)
            assert core.get_item("http.request.uri", span=root_span()) == "http://localhost:8000/.git?q=1"
            assert core.get_item("http.request.headers", span=root_span()) is not None
            query = dict(core.get_item("http.request.query", span=root_span()))
            assert query == {"q": "1"} or query == {"q": ["1"]}

    def test_querystrings(self, interface: Interface, root_span):
        with override_global_config(dict(_asm_enabled=True)):
            self.update_tracer(interface)
            response = interface.client.get("/?a=1&b&c=d")
            assert self.status(response) == 200
            query = dict(core.get_item("http.request.query", span=root_span()))
            assert query in [
                {"a": "1", "b": "", "c": "d"},
                {"a": ["1"], "b": [""], "c": ["d"]},
                {"a": ["1"], "c": ["d"]},
            ]

    def test_no_querystrings(self, interface: Interface, root_span):
        with override_global_config(dict(_asm_enabled=True)):
            self.update_tracer(interface)
            response = interface.client.get("/")
            assert self.status(response) == 200
            assert not core.get_item("http.request.query", span=root_span())

    @pytest.mark.parametrize("asm_enabled", [True, False])
    @pytest.mark.parametrize(
        ("cookies", "attack"),
        [({"mytestingcookie_key": "mytestingcookie_value"}, False), ({"attack": "1' or '1' = '1'"}, True)],
    )
    def test_request_cookies(self, interface: Interface, root_span, get_tag, asm_enabled, cookies, attack):
        with override_global_config(dict(_asm_enabled=asm_enabled)), override_env(
            dict(DD_APPSEC_RULES=rules.RULES_GOOD_PATH)
        ):
            self.update_tracer(interface)
            response = interface.client.get("/", cookies=cookies)
            assert self.status(response) == 200
            if asm_enabled:
                cookies_parsed = dict(core.get_item("http.request.cookies", span=root_span()))
                assert cookies_parsed == cookies
            else:
                assert core.get_item("http.request.cookies", span=root_span()) is None
            str_json = get_tag(APPSEC.JSON)
            if asm_enabled and attack:
                assert str_json is not None, "no JSON tag in root span"
                json_payload = json.loads(str_json)
                assert len(json_payload["triggers"]) == 1
                assert json_payload["triggers"][0]["rule"]["id"] == "crs-942-100"
            else:
                assert str_json is None, f"asm JSON tag in root span: asm_enabled={asm_enabled}, attack={attack}"

    @pytest.mark.parametrize("asm_enabled", [True, False])
    @pytest.mark.parametrize(
        ("encode_payload", "content_type"),
        [
            (urlencode, "application/x-www-form-urlencoded"),
            (json.dumps, "application/json"),
            (payload_to_xml, "text/xml"),
            (payload_to_plain_text, "text/plain"),
        ],
    )
    @pytest.mark.parametrize(
        ("payload_struct", "attack"),
        [({"mytestingbody_key": "mytestingbody_value"}, False), ({"attack": "1' or '1' = '1'"}, True)],
    )
    def test_request_body(
        self,
        interface: Interface,
        root_span,
        get_tag,
        asm_enabled,
        encode_payload,
        content_type,
        payload_struct,
        attack,
    ):
        with override_global_config(dict(_asm_enabled=asm_enabled)):
            self.update_tracer(interface)
            payload = encode_payload(payload_struct)
            response = interface.client.post("/asm/", data=payload, content_type=content_type)
            assert self.status(response) == 200  # Have to add end points in each framework application.

            body = core.get_item("http.request.body", span=root_span())
            if asm_enabled and content_type != "text/plain":
                assert body in [
                    payload_struct,
                    {k: [v] for k, v in payload_struct.items()},
                ]
            else:
                assert not body  # DEV: Flask send {} for text/plain with asm

            str_json = get_tag(APPSEC.JSON)

            if asm_enabled and attack and content_type != "text/plain":
                assert str_json is not None, "no JSON tag in root span"
                json_payload = json.loads(str_json)
                assert len(json_payload["triggers"]) == 1
                assert json_payload["triggers"][0]["rule"]["id"] == "crs-942-100"
            else:
                assert str_json is None, "asm JSON tag in root span"


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
