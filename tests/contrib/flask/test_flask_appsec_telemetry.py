import json

import pytest

from ddtrace.appsec._constants import APPSEC
from ddtrace.internal import _context
from ddtrace.internal.compat import urlencode
from ddtrace.internal.constants import APPSEC_BLOCKED_RESPONSE_JSON
from tests.appsec.test_processor import RULES_GOOD_PATH
from tests.appsec.test_processor import _BLOCKED_IP
from tests.appsec.test_telemety import _assert_generate_metrics
from tests.appsec.test_telemety import mock_telemetry_metrics_writer  # noqa: F401
from tests.contrib.flask import BaseFlaskTestCase
from tests.utils import override_env
from tests.utils import override_global_config


class FlaskAppSecTestCase(BaseFlaskTestCase):
    @pytest.fixture(autouse=True)
    def inject_fixtures(self, mock_telemetry_metrics_writer):  # noqa: F811
        self.mock_telemetry_metrics_writer = mock_telemetry_metrics_writer

    def _aux_appsec_prepare_tracer(self, appsec_enabled=True):
        self.tracer._appsec_enabled = appsec_enabled
        # Hack: need to pass an argument to configure so that the processors are recreated
        self.tracer.configure(api_version="v0.4")

    def test_telemetry_metrics_block(self):
        with override_global_config(dict(_appsec_enabled=True, _telemetry_metrics_enabled=True)), override_env(
            dict(DD_APPSEC_RULES=RULES_GOOD_PATH)
        ):
            self._aux_appsec_prepare_tracer()
            resp = self.client.get("/", headers={"X-Real-Ip": _BLOCKED_IP})
            assert resp.status_code == 403
            if hasattr(resp, "text"):
                assert resp.text == APPSEC_BLOCKED_RESPONSE_JSON

        _assert_generate_metrics(
            self.mock_telemetry_metrics_writer._namespace._metrics_data, is_rule_triggered=True, is_blocked_request=True
        )

    def test_telemetry_metrics_attack(self):
        with override_global_config(dict(_appsec_enabled=True, _telemetry_metrics_enabled=True)):
            self._aux_appsec_prepare_tracer()
            payload = urlencode({"attack": "1' or '1' = '1'"})
            self.client.post("/", data=payload, content_type="application/x-www-form-urlencoded")
            root_span = self.pop_spans()[0]
            query = dict(_context.get_item("http.request.body", span=root_span))
            assert "triggers" in json.loads(root_span.get_tag(APPSEC.JSON))
            assert query == {"attack": "1' or '1' = '1'"}

        _assert_generate_metrics(
            self.mock_telemetry_metrics_writer._namespace._metrics_data,
            is_rule_triggered=True,
            is_blocked_request=False,
        )
