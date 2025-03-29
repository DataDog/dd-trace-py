from urllib.parse import urlencode

import pytest

from ddtrace.appsec._utils import get_triggers
from ddtrace.internal import core
from ddtrace.internal.constants import BLOCKED_RESPONSE_JSON
from tests.appsec.appsec.test_telemetry import _assert_generate_metrics
import tests.appsec.rules as rules
from tests.contrib.flask import BaseFlaskTestCase
from tests.utils import override_global_config


class FlaskAppSecTestCase(BaseFlaskTestCase):
    @pytest.fixture(autouse=True)
    def inject_fixtures(self, telemetry_writer):  # noqa: F811
        self.telemetry_writer = telemetry_writer

    def _aux_appsec_prepare_tracer(self, appsec_enabled=True):
        # Hack: need to pass an argument to configure so that the processors are recreated
        self.tracer._recreate()

    def test_telemetry_metrics_block(self):
        with override_global_config(dict(_asm_enabled=True, _asm_static_rule_file=rules.RULES_GOOD_PATH)):
            self._aux_appsec_prepare_tracer()
            resp = self.client.get("/", headers={"X-Real-Ip": rules._IP.BLOCKED})
            assert resp.status_code == 403
            if hasattr(resp, "text"):
                assert resp.text == BLOCKED_RESPONSE_JSON

        _assert_generate_metrics(
            self.telemetry_writer._namespace.flush(),
            is_rule_triggered=True,
            is_blocked_request=True,
        )

    def test_telemetry_metrics_attack(self):
        with override_global_config(dict(_asm_enabled=True)):
            self._aux_appsec_prepare_tracer()
            payload = urlencode({"attack": "1' or '1' = '1'"})
            self.client.post("/", data=payload, content_type="application/x-www-form-urlencoded")
            root_span = self.pop_spans()[0]
            query = dict(core.get_item("http.request.body", span=root_span))
            assert get_triggers(root_span)
            assert query == {"attack": "1' or '1' = '1'"}

        _assert_generate_metrics(
            self.telemetry_writer._namespace.flush(),
            is_rule_triggered=True,
            is_blocked_request=False,
        )
