from unittest import TestCase
import warnings

from ddtrace.internal.settings._config import Config
from ddtrace.internal.settings.integration import IntegrationConfig


class HooksDeprecationTestCase(TestCase):
    def setUp(self):
        self.config = Config()
        self.config.web = IntegrationConfig(self.config, "web")

    def test_hooks_register_deprecation(self):
        warnings.simplefilter("always")
        with warnings.catch_warnings(record=True) as warns:
            self.config.web.hooks.register("test_hook")
        assert len(warns) == 1
        assert (
            "Hooks.register() is deprecated and is currently a no-op. and will be removed in version '5.0.0': To interact with spans, use get_current_span() or get_current_root_span()."  # noqa:E501
            in str(warns[0].message)
        )

    def test_hooks_on_deprecation(self):
        warnings.simplefilter("always")
        with warnings.catch_warnings(record=True) as warns:
            self.config.web.hooks.on("test_hook")
        assert len(warns) == 1
        assert (
            "Hooks.register() is deprecated and is currently a no-op. and will be removed in version '5.0.0': To interact with spans, use get_current_span() or get_current_root_span()."  # noqa:E501
            in str(warns[0].message)
        )

    def test_hooks_deregister_deprecation(self):
        warnings.simplefilter("always")
        with warnings.catch_warnings(record=True) as warns:
            self.config.web.hooks.deregister("test_hook", lambda: None)
        assert len(warns) == 1
        assert (
            "Hooks.deregister() is deprecated and is currently a no-op. and will be removed in version '5.0.0'"  # noqa:E501
            in str(warns[0].message)
        )

    def test_hooks_emit_deprecation(self):
        warnings.simplefilter("always")
        with warnings.catch_warnings(record=True) as warns:
            self.config.web.hooks.emit("test_hook")
        assert len(warns) == 1
        assert (
            "Hooks.emit() is deprecated and will be removed in version '5.0.0': Use tracer.current_span() or TraceFilters to retrieve and/or modify spans"  # noqa:E501
            in str(warns[0].message)
        )

    def test_hooks_register_decorator_deprecation(self):
        warnings.simplefilter("always")
        with warnings.catch_warnings(record=True) as warns:

            @self.config.web.hooks.register("test_hook")
            def my_hook():
                pass

        assert len(warns) == 1
        assert (
            "Hooks.register() is deprecated and is currently a no-op. and will be removed in version '5.0.0': To interact with spans, use get_current_span() or get_current_root_span()."  # noqa:E501
            in str(warns[0].message)
        )

    def test_hooks_on_decorator_deprecation(self):
        warnings.simplefilter("always")
        with warnings.catch_warnings(record=True) as warns:

            @self.config.web.hooks.on("test_hook")
            def my_hook():
                pass

        assert len(warns) == 1
        assert (
            "Hooks.register() is deprecated and is currently a no-op. and will be removed in version '5.0.0': To interact with spans, use get_current_span() or get_current_root_span()."  # noqa:E501
            in str(warns[0].message)
        )
