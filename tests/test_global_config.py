import mock

from ddtrace import config as global_config
from ddtrace.settings import Config

from .base import BaseTracerTestCase


class GlobalConfigTestCase(BaseTracerTestCase):
    """Test the `Configuration` class that stores integration settings"""
    def setUp(self):
        super(GlobalConfigTestCase, self).setUp()

        self.config = Config()

    def test_registration(self):
        # ensure an integration can register a new list of settings
        settings = {
            'distributed_tracing': True,
        }
        self.config._add('requests', settings)
        self.assertTrue(self.config.requests['distributed_tracing'] is True)

    def test_settings_copy(self):
        # ensure that once an integration is registered, a copy
        # of the settings is stored to avoid side-effects
        experimental = {
            'request_enqueuing': True,
        }
        settings = {
            'distributed_tracing': True,
            'experimental': experimental,
        }
        self.config._add('requests', settings)

        settings['distributed_tracing'] = False
        experimental['request_enqueuing'] = False
        self.assertTrue(self.config.requests['distributed_tracing'])
        self.assertTrue(self.config.requests['experimental']['request_enqueuing'])

    def test_missing_integration_key(self):
        """
        When accessing an unknown integration on a Config object
            No exception is thrown
        """
        # DEV: The test is that no exception is thrown
        self.config.new_integration['some_key']

    def test_global_configuration(self):
        # ensure a global configuration is available in the `ddtrace` module
        self.assertTrue(isinstance(global_config, Config))

    def test_settings_merge(self):
        """
        When calling `config._add()`
            when existing settings exist
                we do not overwrite the existing settings
        """
        self.config.requests['split_by_domain'] = True
        self.config._add('requests', dict(split_by_domain=False))
        self.assertEqual(self.config.requests['split_by_domain'], True)

    def test_settings_add_after_setting(self):
        """
        When calling `config._add()`
            when existing "deep" settings exist
                we do not overwrite the existing settings
        """
        # Configure user supplied value
        self.config.requests['a'] = dict(
            b=dict(
                c=True,
            ),
        )

        # Set the default value
        self.config._add('requests', dict(
            a=dict(
                b=dict(
                    c=False,
                    d=True,
                ),
            ),
        ))

        # We have the user supplied value properly set
        self.assertEqual(self.config.requests['a']['b']['c'], True)
        self.assertEqual(self.config.requests['a']['b']['d'], True)

        # We have the default value property set
        a_item = self.config.requests.get_item('a')
        self.assertEqual(a_item.default['b']['c'], False)
        self.assertEqual(a_item.default['b']['d'], True)

    def test_settings_hook(self):
        """
        When calling `Hooks._emit()`
            When there is a hook registered
                we call the hook as expected
        """
        # Setup our hook
        @self.config.web.hooks.on('request')
        def on_web_request(span):
            span.set_tag('web.request', '/')

        # Create our span
        span = self.tracer.start_span('web.request')
        self.assertTrue('web.request' not in span.meta)

        # Emit the span
        self.config.web.hooks._emit('request', span)

        # Assert we updated the span as expected
        self.assertEqual(span.get_tag('web.request'), '/')

    def test_settings_hook_args(self):
        """
        When calling `Hooks._emit()` with arguments
            When there is a hook registered
                we call the hook as expected
        """
        # Setup our hook
        @self.config.web.hooks.on('request')
        def on_web_request(span, request, response):
            span.set_tag('web.request', request)
            span.set_tag('web.response', response)

        # Create our span
        span = self.tracer.start_span('web.request')
        self.assertTrue('web.request' not in span.meta)

        # Emit the span
        # DEV: The actual values don't matter, we just want to test args + kwargs usage
        self.config.web.hooks._emit('request', span, 'request', response='response')

        # Assert we updated the span as expected
        self.assertEqual(span.get_tag('web.request'), 'request')
        self.assertEqual(span.get_tag('web.response'), 'response')

    def test_settings_hook_args_failure(self):
        """
        When calling `Hooks._emit()` with arguments
            When there is a hook registered that is missing parameters
                we do not raise an exception
        """
        # Setup our hook
        # DEV: We are missing the required "response" argument
        @self.config.web.hooks.on('request')
        def on_web_request(span, request):
            span.set_tag('web.request', request)

        # Create our span
        span = self.tracer.start_span('web.request')
        self.assertTrue('web.request' not in span.meta)

        # Emit the span
        # DEV: This also asserts that no exception was raised
        self.config.web.hooks._emit('request', span, 'request', response='response')

        # Assert we did not update the span
        self.assertTrue('web.request' not in span.meta)

    def test_settings_multiple_hooks(self):
        """
        When calling `Hooks._emit()`
            When there are multiple hooks registered
                we do not raise an exception
        """
        # Setup our hooks
        @self.config.web.hooks.on('request')
        def on_web_request(span):
            span.set_tag('web.request', '/')

        @self.config.web.hooks.on('request')
        def on_web_request2(span):
            span.set_tag('web.status', 200)

        @self.config.web.hooks.on('request')
        def on_web_request3(span):
            span.set_tag('web.method', 'GET')

        # Create our span
        span = self.tracer.start_span('web.request')
        self.assertTrue('web.request' not in span.meta)
        self.assertTrue('web.status' not in span.meta)
        self.assertTrue('web.method' not in span.meta)

        # Emit the span
        self.config.web.hooks._emit('request', span)

        # Assert we updated the span as expected
        self.assertEqual(span.get_tag('web.request'), '/')
        self.assertEqual(span.get_tag('web.status'), '200')
        self.assertEqual(span.get_tag('web.method'), 'GET')

    def test_settings_hook_failure(self):
        """
        When calling `Hooks._emit()`
            When the hook raises an exception
                we do not raise an exception
        """
        # Setup our failing hook
        on_web_request = mock.Mock(side_effect=Exception)
        self.config.web.hooks.register('request')(on_web_request)

        # Create our span
        span = self.tracer.start_span('web.request')

        # Emit the span
        # DEV: This is the test, to ensure no exceptions are raised
        self.config.web.hooks._emit('request', span)
        on_web_request.assert_called()

    def test_settings_no_hook(self):
        """
        When calling `Hooks._emit()`
            When no hook is registered
                we do not raise an exception
        """
        # Create our span
        span = self.tracer.start_span('web.request')

        # Emit the span
        # DEV: This is the test, to ensure no exceptions are raised
        self.config.web.hooks._emit('request', span)

    def test_settings_no_span(self):
        """
        When calling `Hooks._emit()`
            When no span is provided
                we do not raise an exception
        """
        # Setup our hooks
        @self.config.web.hooks.on('request')
        def on_web_request(span):
            span.set_tag('web.request', '/')

        # Emit the span
        # DEV: This is the test, to ensure no exceptions are raised
        self.config.web.hooks._emit('request', None)
