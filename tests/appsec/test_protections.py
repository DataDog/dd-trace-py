from ddtrace import appsec
from tests.utils import TracerTestCase


class TestSqreenLibrary(TracerTestCase):
    def test_report(self):
        appsec.enable()

        with self.trace("test") as span:
            appsec.process_request(span, query="foo=bar")
            assert dict(appsec._mgmt.protections[0].stats) == {"total": 1, "reported": 0}

        with self.trace("test") as span:
            appsec.process_request(span, query="q=<script>alert(1);")
            assert dict(appsec._mgmt.protections[0].stats) == {"total": 2, "reported": 1}
