from unittest import TestCase

import msgpack
import pytest

from ddtrace.internal.ci_visibility import CIVisibility
from ddtrace.trace import Span
from tests.ci_visibility.util import _patch_dummy_writer
from tests.ci_visibility.util import set_up_mock_civisibility


class IsUserProvidedServiceConfigTestCase(TestCase):
    def _scenario_test_is_user_provided_service_true(self):
        from ddtrace import config as ddconfig

        assert ddconfig.service == "some-service"
        assert ddconfig._is_user_provided_service is True

    def _scenario_test_is_user_provided_service_false(self):
        from ddtrace import config as ddconfig

        assert ddconfig.service is None
        assert ddconfig._is_user_provided_service is False


class IsUserProvidedServiceTestTagTestCase(TestCase):
    def assert_is_user_provided_service_equals(self, value):
        payload = msgpack.loads(
            CIVisibility._instance.tracer._span_aggregator.writer._clients[0].encoder._build_payload([[Span("foo")]])[
                0
            ][0]
        )
        assert payload["metadata"]["*"]["_dd.test.is_user_provided_service"] == value

    def _scenario_test_is_user_provided_service_true(self):
        with set_up_mock_civisibility(), _patch_dummy_writer():
            CIVisibility.enable()
            self.assert_is_user_provided_service_equals("true")
            CIVisibility.disable()

    def _scenario_test_is_user_provided_service_false(self):
        with set_up_mock_civisibility(), _patch_dummy_writer():
            CIVisibility.enable()
            self.assert_is_user_provided_service_equals("false")
            CIVisibility.disable()


@pytest.mark.subprocess(env=dict(DD_SERVICE="some-service"), out=None, err=None, command=True, test_assertions=True)
def test_IsUserProvidedServiceConfigTestCase_is_user_provided_service_true():
    from tests.ci_visibility.test_is_user_provided_service import IsUserProvidedServiceConfigTestCase
    from tests.process import case_context

    with case_context(IsUserProvidedServiceConfigTestCase, "_scenario_test_is_user_provided_service_true") as case:
        case._scenario_test_is_user_provided_service_true()


@pytest.mark.subprocess(env={}, out=None, err=None, command=True, test_assertions=True)
def test_IsUserProvidedServiceConfigTestCase_is_user_provided_service_false():
    from tests.ci_visibility.test_is_user_provided_service import IsUserProvidedServiceConfigTestCase
    from tests.process import case_context

    with case_context(IsUserProvidedServiceConfigTestCase, "_scenario_test_is_user_provided_service_false") as case:
        case._scenario_test_is_user_provided_service_false()


@pytest.mark.subprocess(env=dict(DD_SERVICE="some-service"), out=None, err=None, command=True, test_assertions=True)
def test_IsUserProvidedServiceTestTagTestCase_is_user_provided_service_true():
    from tests.ci_visibility.test_is_user_provided_service import IsUserProvidedServiceTestTagTestCase
    from tests.process import case_context

    with case_context(IsUserProvidedServiceTestTagTestCase, "_scenario_test_is_user_provided_service_true") as case:
        case._scenario_test_is_user_provided_service_true()


@pytest.mark.subprocess(env={}, out=None, err=None, command=True, test_assertions=True)
def test_IsUserProvidedServiceTestTagTestCase_is_user_provided_service_false():
    from tests.ci_visibility.test_is_user_provided_service import IsUserProvidedServiceTestTagTestCase
    from tests.process import case_context

    with case_context(IsUserProvidedServiceTestTagTestCase, "_scenario_test_is_user_provided_service_false") as case:
        case._scenario_test_is_user_provided_service_false()
