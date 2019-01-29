import mock

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

    def test_integration___init__(self):
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

    def test_integration_update(self):
        """
        When calling `integation.update()`
            With a ``dict``
                We update the settings as expected
            With a kwarg
                We update the settings as expected
            With an ``IntegrationConfigItem``
                We update the default value of the settings only
        """
        self.integration.update(dict(key='value'))
        self.assert_setting('key', value='value')

        self.integration.update(key='new_value')
        self.assert_setting('key', value='new_value')

        self.integration.update(key=IntegrationConfigItem('key', default='default_value'))
        self.assert_setting('key', value='new_value', default='default_value')

    def test_integration_get(self):
        """
        When calling `integration.get()`
            When the setting exists
                We return the value of the setting
            When the setting does not exist
                We return the supplied `default` value or else ``None``
        """
        self.integration.key = 'value'
        self.assertEqual(self.integration.get('key'), 'value')

        self.assertIsNone(self.integration.get('unknown'))
        self.assertEqual(self.integration.get('unknown', default='default'), 'default')

    def test_integration___get_item__(self):
        """
        When accessing a property with item accessors
            When an item does not exist
                We return ``None``
                We create an ``IntegrationConfigItem`` for it
            When an item exists
                We return the value set
        """
        # Setting does not exist
        self.assert_setting_missing('test')
        # Gives us ``None`` if accessed
        self.assertIsNone(self.integration['test'])
        # Creates the item for us
        self.assert_setting('test')

        self.integration.key = 'value'
        self.assertEqual(self.integration['key'], 'value')

    def test_integration___set_item__(self):
        """
        When setting a property with item accessors
            When an item does not exist
                An item is added
            When an item exists
                The value of the item is updated
            When an item is set as an ``IntegrationConfigItem``
                The default value of the item is updated
        """
        self.assert_setting_missing('test')
        self.integration['test'] = 'value'
        self.assert_setting('test', value='value')

        self.integration['test'] = 'new_value'
        self.assert_setting('test', value='new_value')

        self.integration['test'] = IntegrationConfigItem('tests', default='default_value')
        self.assert_setting('test', value='new_value', default='default_value')

    def test_integration_get_item(self):
        """
        When calling `integration.get_item()`
            When an item does not exist
                We return ``None``
            When an item exists
                We get the ``ItegrationConfigItem`` instead of the value
        """
        self.assertIsNone(self.integration.get_item('test'))

        self.integration.test = 'value'
        item = self.integration.get_item('test')
        self.assertIsInstance(item, IntegrationConfigItem)
        self.assertEqual(item.name, 'test')
        self.assertEqual(item.value, 'value')
        self.assertIsNone(item.default)

    def test_integration___getattr__(self):
        """
        When accessing a property with attribute accessors
            When an item does not exist
                We return ``None``
                We create an ``IntegrationConfigItem`` for it
            When an item exists
                We return the value set
        """
        # Setting does not exist
        self.assert_setting_missing('test')
        # Gives us ``None`` if accessed
        self.assertIsNone(self.integration.test)
        # Creates the item for us
        self.assert_setting('test')

        self.integration['key'] = 'value'
        self.assertEqual(self.integration.key, 'value')

    def test_integration___setattr__(self):
        """
        When setting a property with attribute accessors
            When an item does not exist
                An item is added
            When an item exists
                The value of the item is updated
            When an item is set as an ``IntegrationConfigItem``
                The default value of the item is updated
        """
        self.assert_setting_missing('test')
        self.integration.test = 'value'
        self.assert_setting('test', value='value')

        self.integration.test = 'new_value'
        self.assert_setting('test', value='new_value')

        self.integration.test = IntegrationConfigItem('tests', default='default_value')
        self.assert_setting('test', value='new_value', default='default_value')

    def test_integration___deepcopy__(self):
        """
        When calling `integration.__deepcopy__()`
            We return back a copy of the integration config
        """
        # Test with something that will be passed as reference
        # We do this to ensure a copy with no references is copied
        self.integration.setting = dict(a='b')
        item = self.integration.get_item('setting')

        new_integration = self.integration.__deepcopy__()
        new_item = new_integration.get_item('setting')

        # Assert that no references are the same
        self.assertNotEqual(new_integration, self.integration)
        self.assertNotEqual(item, new_item)

        # DEV: Assert the object ids are different (not the same object)
        self.assertNotEqual(id(item.value), id(new_item.value))
        self.assertEqual(item.value, new_item.value)

        # Modifying one doesn't modify the other
        item.value['a'] = 'c'
        self.assertEqual(new_item.value['a'], 'b')

    def test_integration_copy(self):
        """
        When calling `integration.copy()`
            We return back a copy of the integration config
        """
        # Test with something that will be passed as reference
        # We do this to ensure a copy with no references is copied
        self.integration.setting = dict(a='b')
        item = self.integration.get_item('setting')

        new_integration = self.integration.__deepcopy__()
        new_item = new_integration.get_item('setting')

        # Assert that no references are the same
        self.assertNotEqual(new_integration, self.integration)
        self.assertNotEqual(item, new_item)

        # DEV: Assert the object ids are different (not the same object)
        self.assertNotEqual(id(item.value), id(new_item.value))
        self.assertEqual(item.value, new_item.value)

        # Modifying one doesn't modify the other
        item.value['a'] = 'c'
        self.assertEqual(new_item.value['a'], 'b')

    def test_integration_header_is_traced(self):
        """
        When calling `integration.header_is_traced()`
            When integration specific tracing is not configured
                We call `integration.global_config.header_is_traced()`
            When integration specific tracing is configured
                We call `integration.http.header_is_traced()`
        """
        # Setup our mocks
        self.integration.global_config.header_is_traced = mock.MagicMock(
            self.integration.global_config.header_is_traced,
        )
        self.integration.http.header_is_traced = mock.MagicMock(
            self.integration.http.header_is_traced,
        )

        # Assert that global function is called when no header tracing is configured
        self.assertFalse(self.integration.http.is_header_tracing_configured)
        self.integration.header_is_traced('test_header')
        self.integration.global_config.header_is_traced.assert_called_once_with('test_header')
        self.integration.http.header_is_traced.assert_not_called()

        # Reset call counter on global config
        self.integration.global_config.header_is_traced.reset_mock()

        # Configure integration specific header tracing
        self.integration.http.trace_headers(['test_header'])

        # Assert integration specific tracing is called when available
        self.integration.header_is_traced('test_header')
        self.integration.http.header_is_traced.assert_called_once_with('test_header')
        self.integration.global_config.header_is_traced.assert_not_called()


class IntegrationConfigItemTestCase(BaseTestCase):
    def test_item___init__(self):
        """
        When calling `item.__init__()`
            We properly configure the item
        """
        # Only setting `name`
        item = IntegrationConfigItem('name')
        self.assertEqual(item.name, 'name')
        self.assertEqual(item.value, IntegrationConfigItem.UNSET)
        self.assertIsNone(item.default)
        self.assertTrue(item.has_env_var)
        self.assertIsNone(item.env_override)
        self.assertTrue(item.allow_none)

        # Setting default
        item = IntegrationConfigItem('name', default='default_value')
        self.assertEqual(item.default, 'default_value')

        # Setting has_env_var
        item = IntegrationConfigItem('name', has_env_var=False)
        self.assertFalse(item.has_env_var)

        # Setting env_override
        item = IntegrationConfigItem('name', env_override='MY_ENV_NAME')
        self.assertTrue(item.has_env_var)
        self.assertEqual(item.env_override, 'MY_ENV_NAME')

        # Setting env_override and has_env_var to False
        # DEV: `has_env_var` is forced to `True` when `env_override` is set
        item = IntegrationConfigItem('name', has_env_var=False, env_override='MY_ENV_NAME')
        self.assertTrue(item.has_env_var)
        self.assertEqual(item.env_override, 'MY_ENV_NAME')

        # Setting allow_none
        item = IntegrationConfigItem('name', allow_none=False)
        self.assertFalse(item.allow_none)

        # `_cast` is called on our default value
        with mock.patch.object(IntegrationConfigItem, '_cast', return_value='default_value') as cast:
            item = IntegrationConfigItem('name', default='default_value')
            self.assertEqual(item.default, 'default_value')
            cast.assert_called_once_with('default_value', allow_none=True)

    def test_item_alias(self):
        """
        When calling `item.alias()`
            We get an ``IntegrationConfigItemAlias`` pointing to our item's name
        """
        item = IntegrationConfigItem('item')

        alias = item.alias('alias')
        self.assertIsInstance(alias, IntegrationConfigItemAlias)
        self.assertEqual(alias.name, 'alias')
        self.assertEqual(alias.alias, 'item')

    def test_item___get__(self):
        """
        """

    def test_item___set__(self):
        """
        """

    def test_item___deepcopy__(self):
        """
        """

    def test_item_copy(self):
        """
        """
