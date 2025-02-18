from tests.utils import TracerTestCase
from tests.subprocesstest import run_in_subprocess

class IsUserProvidedServiceTestCase(TracerTestCase):
    @run_in_subprocess(env_overrides=dict(DD_SERVICE="some-service"))
    def test_is_user_provided_service_true(self):
        breakpoint()
