from unittest import TestCase

import pytest

from ddtrace.internal.settings._config import Config
from ddtrace.internal.settings.integration import IntegrationConfig


class HooksDeprecationTestCase(TestCase):
    """Test that Hooks class methods raise deprecation warnings"""

    def setUp(self):
        self.config = Config()
        self.config.web = IntegrationConfig(self.config, "web")

    def test_hooks_register_deprecation(self):
        """
        When calling `config.hooks.register()`
            A deprecation warning is raised
        """
        with pytest.warns(match="Hooks.register\\(\\) is deprecated and is currently a no-op"):
            self.config.web.hooks.register("test_hook", lambda: None)

    def test_hooks_on_deprecation(self):
        """
        When calling `config.hooks.on()` (alias for register)
            A deprecation warning is raised
        """
        with pytest.warns(match="Hooks.register\\(\\) is deprecated and is currently a no-op"):
            self.config.web.hooks.on("test_hook", lambda: None)

    def test_hooks_deregister_deprecation(self):
        """
        When calling `config.hooks.deregister()`
            A deprecation warning is raised
        """
        with pytest.warns(match="Hooks.deregister\\(\\) is deprecated and is currently a no-op"):
            self.config.web.hooks.deregister("test_hook", lambda: None)

    def test_hooks_emit_deprecation(self):
        """
        When calling `config.hooks.emit()`
            A deprecation warning is raised
        """
        with pytest.warns(match="Hooks.emit\\(\\) is deprecated and is currently a no-op"):
            self.config.web.hooks.emit("test_hook")

    def test_hooks_register_decorator_deprecation(self):
        """
        When calling `config.hooks.register()` as a decorator
            A deprecation warning is raised
        """
        with pytest.warns(match="Hooks.register\\(\\) is deprecated and is currently a no-op"):

            @self.config.web.hooks.register("test_hook")
            def my_hook():
                pass

    def test_hooks_on_decorator_deprecation(self):
        """
        When calling `config.hooks.on()` as a decorator
            A deprecation warning is raised
        """
        with pytest.warns(match="Hooks.register\\(\\) is deprecated and is currently a no-op"):

            @self.config.web.hooks.on("test_hook")
            def my_hook():
                pass
