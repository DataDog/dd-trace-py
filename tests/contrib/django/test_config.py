import mock
from ddtrace.contrib.django.conf import DatadogSettings
from django.test import TestCase, override_settings
from nose.tools import eq_


class DjangoSettingsTest(TestCase):

    @override_settings(DATADOG_TRACE={'TAGS': {'my_tag': 'my_value'}})
    def test_user_settings_defaults_to_global_if_not_provided(self):
        settings = DatadogSettings(None)
        eq_(settings.TAGS, {'my_tag': 'my_value'})

    @override_settings(DATADOG_TRACE={'TAGS': {'my_tag': 'from_settings'}})
    def test_user_settings_can_be_provided(self):
        settings = DatadogSettings({'TAGS': {'my_tag': 'provided'}})
        eq_(settings.TAGS, {'my_tag': 'provided'})

    @override_settings(DATADOG_TRACE={'DEFAULT_SERVICE': 'my-special-service'})
    def test_cache_service_name_defaults_to_default_service(self):
        settings = DatadogSettings(None)
        eq_(settings.cache_service_name, 'my-special-service')

    @override_settings(DATADOG_TRACE={'DEFAULT_SERVICE': 'my-special-service', 'CACHE_SERVICE_NAME': 'overridden'})
    def test_cache_service_name_can_be_overridden(self):
        settings = DatadogSettings(None)
        eq_(settings.cache_service_name, 'overridden')

    @override_settings(DATADOG_TRACE={'CACHE_SERVICE_NAME': ''})
    def test_cache_service_name_can_be_empty(self):
        settings = DatadogSettings(None)
        eq_(settings.cache_service_name, 'django')

    @override_settings(DATADOG_TRACE={'CACHE_SERVICE_NAME': None})
    def test_cache_service_name_can_be_none(self):
        settings = DatadogSettings(None)
        eq_(settings.cache_service_name, 'django')

    @override_settings(DATADOG_TRACE={'DEFAULT_SERVICE': 'my-special-service', 'CACHE_SERVICE_NAME': 123})
    @mock.patch('ddtrace.contrib.django.conf.log')
    def test_cache_service_name_does_fail_if_invalid_log_warning(self, mock_log):
        settings = DatadogSettings(None)
        # Even if the value of 'CACHE_SERVICE_NAME' is invalid, we do not want to fail. We fallback to the
        # default service name but we log a warn.
        eq_(settings.cache_service_name, 'my-special-service')
        mock_log.warning.assert_called_with('Invalid setting "CACHE_SERVICE_NAME". '
                                            'The setting MUST be either None or a string.')
