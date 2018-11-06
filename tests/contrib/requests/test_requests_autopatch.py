from nose.tools import eq_

from .test_requests import BaseRequestTestCase, URL_200


class RequestsAutopatchTestCase(BaseRequestTestCase):
    def tearDown(self):
        pass

    def test_idempotence(self):
        """Ensure that we aren't instrumenting twice."""
        from ddtrace.contrib.requests import patch

        # requests should have already been patched by ddtrace-run, now patch again
        patch()
        out = self.session.get(URL_200)
        eq_(out.status_code, 200)

        # validation: should only be 1 span
        spans = self.tracer.writer.pop()
        eq_(len(spans), 1)
