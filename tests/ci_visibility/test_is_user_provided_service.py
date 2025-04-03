import msgpack

from ddtrace.internal.ci_visibility import CIVisibility
from ddtrace.trace import Span
from tests.ci_visibility.util import _patch_dummy_writer
from tests.ci_visibility.util import set_up_mock_civisibility
from tests.subprocesstest import SubprocessTestCase
from tests.subprocesstest import run_in_subprocess


class IsUserProvidedServiceConfigTestCase(SubprocessTestCase):
    @run_in_subprocess(env_overrides=dict(DD_SERVICE="some-service"))
    def test_is_user_provided_service_true(self):
        from ddtrace import config as ddconfig

        assert ddconfig.service == "some-service"
        assert ddconfig._is_user_provided_service is True

    @run_in_subprocess()
    def test_is_user_provided_service_false(self):
        from ddtrace import config as ddconfig

        assert ddconfig.service is None
        assert ddconfig._is_user_provided_service is False


class IsUserProvidedServiceTestTagTestCase(SubprocessTestCase):
    def assert_is_user_provided_service_equals(self, value):
        payload = msgpack.loads(
            CIVisibility._instance.tracer._writer._clients[0].encoder._build_payload([[Span("foo")]])
        )
        assert payload["metadata"]["*"]["_dd.test.is_user_provided_service"] == value

    @run_in_subprocess(env_overrides=dict(DD_SERVICE="some-service"))
    def test_is_user_provided_service_true(self):
        with set_up_mock_civisibility(), _patch_dummy_writer():
            CIVisibility.enable()
            self.assert_is_user_provided_service_equals("true")
            CIVisibility.disable()

    @run_in_subprocess()
    def test_is_user_provided_service_false(self):
        with set_up_mock_civisibility(), _patch_dummy_writer():
            CIVisibility.enable()
            self.assert_is_user_provided_service_equals("false")
            CIVisibility.disable()
