import mock
import molten
from molten.testing import TestClient

from ddtrace.contrib.internal.molten.patch import patch
from ddtrace.contrib.internal.molten.patch import unpatch
from ddtrace.internal.runtime import MICROVM_RUN_HOOK_PATH
from tests.utils import TracerTestCase


def greet():
    return "Greetings"


class MoltenMicrovmIdentityRefreshTestCase(TracerTestCase):
    """patch_app_call() must pass method/path to maybe_refresh_identity(), so it detects the
    MicroVM /run hook without app changes.
    """

    def setUp(self):
        super().setUp()
        patch()
        self.app = molten.App(routes=[molten.Route("/greet", greet)])
        self.client = TestClient(self.app)

    def tearDown(self):
        super().tearDown()
        unpatch()

    def test_microvm_run_hook_request(self):
        """No route is registered at the hook path: patch_app_call() wraps the raw WSGI entry
        point, ahead of molten's router, so it still fires on the 404.
        """
        with mock.patch("ddtrace.contrib.internal.molten.patch.maybe_refresh_identity") as m:
            response = self.client.request("POST", MICROVM_RUN_HOOK_PATH)

        self.assertEqual(response.status_code, 404)
        m.assert_called_once_with("POST", MICROVM_RUN_HOOK_PATH)

    def test_other_request(self):
        with mock.patch("ddtrace.contrib.internal.molten.patch.maybe_refresh_identity") as m:
            response = self.client.request("GET", "/greet")

        self.assertEqual(response.status_code, 200)
        m.assert_called_once_with("GET", "/greet")
