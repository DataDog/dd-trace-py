from ddtrace.settings.config import Config
from ddtrace.settings.integration import (
    IntegrationConfigItemBase, IntegrationConfigItem, IntegrationConfigItemAlias,
    BoolIntegrationConfigItem, IntIntegrationConfigItem, FloatIntegrationConfigItem,
    IntegrationConfig,
)
from ddtrace.settings.hooks import Hooks
from ddtrace.settings.http import HttpConfig
from ..base import BaseTestCase


class IntegrationConfigTestCase(BaseTestCase):
    def setUp(self):
        super(IntegrationConfigTestCase, self).setUp()
        self.config = Config()
        self.integration = IntegrationConfig(self.config, 'integration_name')

    def tearDown(self):
        super(IntegrationConfigTestCase, self).tearDown()
        delattr(self, 'integration')
        delattr(self, 'config')

    def assert_has_setting(self, name):
        keys = set(self.integration.keys())
        self.assertIn(name, keys)

    def assert_setting_missing(self, name):
        keys = set(self.integration.keys())
        self.assertNotIn(name, keys)

    def assert_setting(self, name, value=IntegrationConfigItem.UNSET, default=None):
        self.assert_has_setting(name)

        item = self.integration.get_item(name)
        self.assertIsNotNone(item)

        self.assertEqual(item.value, value)
        self.assertEqual(item.default, default)

    def test_integration_config_init(self):
        """
        When creating a new ``IntegrationConfig``
            We have the expected default properties/value
        """
        # Assert required properties exist
        self.assertEqual(self.integration.integration_name, 'integration_name')
        self.assertEqual(self.integration.global_config, self.config)
        self.assertIsInstance(self.integration.hooks, Hooks)
        self.assertIsInstance(self.integration.http, HttpConfig)

        # Assert default `event_sample_rate` configuration option is present
        self.assert_setting('event_sample_rate')
        self.assertIsNone(self.integration.event_sample_rate)

    def test_integration_unknown_setting(self):
        """
        When accessing a previously unset setting on an ``IntegrationConfig``
            We have a default ``IntegrationConfigItem``
        """
        # Ensure we don't have the setting already
        self.assert_setting_missing('test')

        # Accessing for the first time is `None`
        self.assertIsNone(self.integration.test)

        # Assert default item settings are used
        self.assert_setting('test')

        # Set a value other than `None`
        self.integration.test = True
        self.assertTrue(self.integration.test)
        self.assert_setting('test', value=True)
