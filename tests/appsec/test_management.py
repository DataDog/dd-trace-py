from ddtrace import appsec
from tests.utils import TracerTestCase


class TestManagement(TracerTestCase):
    def test_enable(self):
        # Load default rules
        appsec.enable()
        # Check the Sqreen Library was successfully loaded
        assert len(appsec._mgmt.protections) == 1

        with self.trace("test") as span:
            appsec.process_request(span, method="GET")

        appsec.disable()
        assert appsec._mgmt.protections == []

    def test_enable_wrong_rules(self):
        with self.override_env(dict(DD_APPSEC_RULES="/nonexistent")):
            # Enable/disable must not crash but display errors in the logs
            appsec.enable()
            assert appsec._mgmt.protections == []
            appsec.disable()
            assert appsec._mgmt.protections == []
