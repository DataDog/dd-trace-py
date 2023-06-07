import base64
import gzip
import json
import sys

from flask import request
import pytest

from ddtrace import config
from ddtrace.contrib.sqlite3.patch import patch
from tests.appsec.test_processor import RULES_SRB
from tests.contrib.flask import BaseFlaskTestCase
from tests.utils import override_env
from tests.utils import override_global_config


def get_response_body(response):
    if hasattr(response, "text"):
        return response.text
    return response.data.decode("utf-8")


class FlaskAppSecTestCase(BaseFlaskTestCase):
    @pytest.fixture(autouse=True)
    def inject_fixtures(self, caplog):
        self._caplog = caplog

    def setUp(self):
        super(FlaskAppSecTestCase, self).setUp()
        patch()

    def _aux_appsec_prepare_tracer(self, appsec_enabled=True, iast_enabled=False):
        self.tracer._appsec_enabled = appsec_enabled
        self.tracer._iast_enabled = iast_enabled
        # Hack: need to pass an argument to configure so that the processors are recreated
        self.tracer.configure(api_version="v0.4")

    @pytest.mark.skipif((sys.version_info.major, sys.version_info.minor) < (3, 7), reason="python<3.7 not supported")
    def test_api_content(self):
        @self.app.route("/response-header/<string:str_param>", methods=["POST"])
        def specific_reponse(str_param):
            data = request.get_json()
            query_params = request.args
            data["validate"] = True
            data["value"] = str_param
            return data, query_params

        payload = {"key": "secret", "ids": [0, 1, 2, 3]}

        with override_global_config(dict(_appsec_enabled=True, _api_security_enabled=True)), override_env(
            dict(DD_APPSEC_RULES=RULES_SRB)
        ):
            self._aux_appsec_prepare_tracer()
            resp = self.client.post(
                "/response-header/posting?extended=345",
                data=json.dumps(payload),
                content_type="application/json",
            )
            assert resp.status_code == 200
            root_span = self.pop_spans()[0]
            assert config._api_security_enabled

            for name, expected_value in [
                ("_dd.schema.req.body", [{"key": [8], "ids": [[[4]], {"len": 4}]}]),
                (
                    "_dd.schema.req.headers",
                    [{"User-Agent": [8], "Host": [8], "Content-Type": [8], "Content-Length": [8]}],
                ),
                ("_dd.schema.req.query", [{"extended": [8]}]),
                ("_dd.schema.req.params", [{"str_param": [8]}]),
                ("_dd.schema.res.headers", [{"Content-Type": [8], "Content-Length": [8], "extended": [8]}]),
                # ("_dd.schema.res.body", [{"ids": [[[4]], {"len": 4}], "key": [8], "validate": [2], "value": [8]}]),
            ]:
                value = root_span.get_tag(name)
                assert value
                api = json.loads(gzip.decompress(base64.b64decode(value)).decode())
                assert api == expected_value

        # appsec disabled must not block
        with override_global_config(dict(_appsec_enabled=False, _api_security_enabled=False)), override_env(
            dict(DD_APPSEC_RULES=RULES_SRB)
        ):
            self._aux_appsec_prepare_tracer(appsec_enabled=False)
            resp = self.client.post(
                "/response-header/abcdef",
                data=json.dumps(payload),
                content_type="application/json",
            )

            assert resp.status_code == 200
            root_span = self.pop_spans()[0]
            assert not config._api_security_enabled
            value = root_span.get_tag("_dd.schema.req.body")
            assert value is None
