from tests.utils import TracerTestCase
from tests.subprocesstest import SubprocessTestCase
from tests.subprocesstest import run_in_subprocess
from tests.ci_visibility.util import _ci_override_env
from tests.ci_visibility.util import set_up_mock_civisibility
from tests.ci_visibility.util import _patch_dummy_writer
from ddtrace.internal.ci_visibility import CIVisibility
import msgpack
from ddtrace.trace import Span


class IsUserProvidedServiceConfigTestCase(SubprocessTestCase):
    @run_in_subprocess(env_overrides=dict(DD_SERVICE="some-service"))
    def test_is_user_provided_service_true(self):
        from ddtrace import config as ddconfig
        assert ddconfig.service == "some-service"
        assert ddconfig._is_user_provided_service == True

    @run_in_subprocess()
    def test_is_user_provided_service_false(self):
        from ddtrace import config as ddconfig
        assert ddconfig.service is None
        assert ddconfig._is_user_provided_service == False


class IsUserProvidedServiceTestTagTestCase(TracerTestCase):
    def tearDown(self):
        try:
            if CIVisibility.enabled:
                CIVisibility.disable()
        except Exception:
            # no-dd-sa:python-best-practices/no-silent-exception
            pass

    def assert_is_user_provided_service_equals(self, value):
        payload = msgpack.loads(
            CIVisibility._instance.tracer._writer._clients[0].encoder._build_payload([[Span("foo")]])
        )
        assert payload["metadata"]["*"]["_dd.test.is_user_provided_service"] == value

    def test_is_user_provided_service_true(self):
        with _ci_override_env(dict()), set_up_mock_civisibility(), _patch_dummy_writer():
            from ddtrace import config as ddconfig
            ddconfig._is_user_provided_service = True
            CIVisibility.enable()
            self.assert_is_user_provided_service_equals("true")
            CIVisibility.disable()

    def test_is_user_provided_service_false(self):
        with _ci_override_env(dict()), set_up_mock_civisibility(), _patch_dummy_writer():
            CIVisibility.enable()
            self.assert_is_user_provided_service_equals("false")
            CIVisibility.disable()
