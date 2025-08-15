from unittest import TestCase

import mock
import pytest

from ddtrace import config as global_config
from ddtrace.settings._config import Config
from ddtrace.settings.integration import IntegrationConfig

from ..utils import DummyTracer
from ..utils import override_env


class GlobalConfigTestCase(TestCase):
    """Test the `Configuration` class that stores integration settings"""

    def setUp(self):
        self.config = Config()
        self.config.web = IntegrationConfig(self.config, "web")
        self.tracer = DummyTracer()

    def test_registration(self):
        # ensure an integration can register a new list of settings
        settings = {
            "distributed_tracing": True,
        }
        self.config._add("requests", settings)
        assert self.config.requests["distributed_tracing"] is True

    def test_settings_copy(self):
        # ensure that once an integration is registered, a copy
        # of the settings is stored to avoid side-effects
        experimental = {
            "request_enqueuing": True,
        }
        settings = {
            "distributed_tracing": True,
            "experimental": experimental,
        }
        self.config._add("requests", settings)

        settings["distributed_tracing"] = False
        experimental["request_enqueuing"] = False
        assert self.config.requests["distributed_tracing"] is True
        assert self.config.requests["experimental"]["request_enqueuing"] is True

    def test_missing_integration_key(self):
        # ensure a meaningful exception is raised when an integration
        # that is not available is retrieved in the configuration
        # object
        with pytest.raises(KeyError) as e:
            self.config.web["some_key"]

        assert isinstance(e.value, KeyError)

    def test_missing_integration(self):
        with pytest.raises(AttributeError) as e:
            self.config.integration_that_does_not_exist

        assert isinstance(e.value, AttributeError)
        assert e.value.args[0] == (
            "<class 'ddtrace.settings._config.Config'> object has no attribute "
            "integration_that_does_not_exist, integration_that_does_not_exist is not a valid configuration"
        )

    def test_global_configuration(self):
        # ensure a global configuration is available in the `ddtrace` module
        assert isinstance(global_config, Config)

    def test_settings_merge(self):
        """
        When calling `config._add()`
            when existing settings exist
                we do not overwrite the existing settings
        """
        self.config.requests["split_by_domain"] = True
        self.config._add("requests", dict(split_by_domain=False))
        assert self.config.requests["split_by_domain"] is True

    def test_settings_overwrite(self):
        """
        When calling `config._add(..., merge=False)`
            when existing settings exist
                we overwrite the existing settings
        """
        self.config.requests["split_by_domain"] = True
        self.config._add("requests", dict(split_by_domain=False), merge=False)
        assert self.config.requests["split_by_domain"] is False

    def test_settings_merge_deep(self):
        """
        When calling `config._add()`
            when existing "deep" settings exist
                we do not overwrite the existing settings
        """
        self.config.requests["a"] = dict(
            b=dict(
                c=True,
            ),
        )
        self.config._add(
            "requests",
            dict(
                a=dict(
                    b=dict(
                        c=False,
                        d=True,
                    ),
                ),
            ),
        )
        assert self.config.requests["a"]["b"]["c"] is True
        assert self.config.requests["a"]["b"]["d"] is True

    def test_settings_hook(self):
        """
        When calling `Hooks.emit()`
            When there is a hook registered
                we call the hook as expected
        """

        # Setup our hook
        @self.config.web.hooks.on("request")
        def on_web_request(span):
            span.set_tag("web.request", "/")

        # Create our span
        with self.tracer.start_span("web.request") as span:
            assert "web.request" not in span.get_tags()

            # Emit the span
            self.config.web.hooks.emit("request", span)

            # Assert we updated the span as expected
            assert span.get_tag("web.request") == "/"

    def test_settings_hook_args(self):
        """
        When calling `Hooks.emit()` with arguments
            When there is a hook registered
                we call the hook as expected
        """

        # Setup our hook
        @self.config.web.hooks.on("request")
        def on_web_request(span, request, response):
            span.set_tag("web.request", request)
            span.set_tag("web.response", response)

        # Create our span
        with self.tracer.start_span("web.request") as span:
            assert "web.request" not in span.get_tags()

            # Emit the span
            # DEV: The actual values don't matter, we just want to test args + kwargs usage
            self.config.web.hooks.emit("request", span, "request", response="response")

            # Assert we updated the span as expected
            assert span.get_tag("web.request") == "request"
            assert span.get_tag("web.response") == "response"

    def test_settings_hook_args_failure(self):
        """
        When calling `Hooks.emit()` with arguments
            When there is a hook registered that is missing parameters
                we do not raise an exception
        """

        # Setup our hook
        # DEV: We are missing the required "response" argument
        @self.config.web.hooks.on("request")
        def on_web_request(span, request):
            span.set_tag("web.request", request)

        # Create our span
        with self.tracer.start_span("web.request") as span:
            assert "web.request" not in span.get_tags()

            # Emit the span
            # DEV: This also asserts that no exception was raised
            self.config.web.hooks.emit("request", span, "request", response="response")

            # Assert we did not update the span
            assert "web.request" not in span.get_tags()

    def test_settings_multiple_hooks(self):
        """
        When calling `Hooks.emit()`
            When there are multiple hooks registered
                we do not raise an exception
        """

        # Setup our hooks
        @self.config.web.hooks.on("request")
        def on_web_request(span):
            span.set_tag("web.request", "/")

        @self.config.web.hooks.on("request")
        def on_web_request2(span):
            span.set_tag("web.status", 200)

        @self.config.web.hooks.on("request")
        def on_web_request3(span):
            span.set_tag("web.method", "GET")

        # Create our span
        with self.tracer.start_span("web.request") as span:
            assert "web.request" not in span.get_tags()
            assert "web.status" not in span.get_metrics()
            assert "web.method" not in span.get_tags()

            # Emit the span
            self.config.web.hooks.emit("request", span)

            # Assert we updated the span as expected
            assert span.get_tag("web.request") == "/"
            assert span.get_metric("web.status") == 200
            assert span.get_tag("web.method") == "GET"

    def test_settings_hook_failure(self):
        """
        When calling `Hooks.emit()`
            When the hook raises an exception
                we do not raise an exception
        """
        # Setup our failing hook
        on_web_request = mock.Mock(side_effect=Exception)
        self.config.web.hooks.register("request")(on_web_request)

        # Create our span
        with self.tracer.start_span("web.request") as span:
            # Emit the span
            # DEV: This is the test, to ensure no exceptions are raised
            self.config.web.hooks.emit("request", span)
            on_web_request.assert_called()

    def test_settings_no_hook(self):
        """
        When calling `Hooks.emit()`
            When no hook is registered
                we do not raise an exception
        """
        # Create our span
        with self.tracer.start_span("web.request") as span:
            # Emit the span
            # DEV: This is the test, to ensure no exceptions are raised
            self.config.web.hooks.emit("request", span)

    def test_settings_no_span(self):
        """
        When calling `Hooks.emit()`
            When no span is provided
                we do not raise an exception
        """

        # Setup our hooks
        @self.config.web.hooks.on("request")
        def on_web_request(span):
            span.set_tag("web.request", "/")

        # Emit the span
        # DEV: This is the test, to ensure no exceptions are raised
        self.config.web.hooks.emit("request", None)

    def test_dd_version(self):
        c = Config()
        assert c.version is None

        with override_env(dict(DD_VERSION="1.2.3")):
            c = Config()
            assert c.version == "1.2.3"

            c.version = "4.5.6"
            assert c.version == "4.5.6"

    def test_dd_env(self):
        c = Config()
        assert c.env is None

        with override_env(dict(DD_ENV="prod")):
            c = Config()
            assert c.env == "prod"

            # manual override still possible
            c.env = "prod-staging"
            assert c.env == "prod-staging"

    def test_dd_service_mapping(self):
        c = Config()
        assert c.service_mapping == {}

        with override_env(dict(DD_SERVICE_MAPPING="foobar:bar,snafu:foo")):
            c = Config()
            assert c.service_mapping == {"foobar": "bar", "snafu": "foo"}
