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
        @self.app.route("/response-header/", methods=["POST"])
        def specific_reponse():
            data = request.get_json()
            data["validate"] = True
            return data

        payload = {"key": "secret", "ids": [0, 1, 2, 3]}

        with override_global_config(dict(_appsec_enabled=True, _api_security_enabled=True)), override_env(
            dict(DD_APPSEC_RULES=RULES_SRB)
        ):
            self._aux_appsec_prepare_tracer()
            resp = self.client.post(
                "/response-header/",
                data=json.dumps(payload),
                content_type="application/json",
            )
            assert resp.status_code == 200
            root_span = self.pop_spans()[0]
            assert config._api_security_enabled
            value = root_span.get_tag("_dd.schema.req.body")
            assert value
            api = json.loads(gzip.decompress(base64.b64decode(value)).decode())
            assert api == [{"key": [8], "ids": [[[4]], {"len": 4}]}]

        # appsec disabled must not block
        with override_global_config(dict(_appsec_enabled=False, _api_security_enabled=False)), override_env(
            dict(DD_APPSEC_RULES=RULES_SRB)
        ):
            self._aux_appsec_prepare_tracer(appsec_enabled=False)
            resp = self.client.post(
                "/response-header/",
                data=json.dumps(payload),
                content_type="application/json",
            )

            assert resp.status_code == 200
            root_span = self.pop_spans()[0]
            assert not config._api_security_enabled
            value = root_span.get_tag("_dd.schema.req.body")
            assert value is None
