from unittest import TestCase

import pytest

from ddtrace import config as global_config
from ddtrace.internal.settings._config import Config
from ddtrace.internal.settings.integration import IntegrationConfig

from ..utils import override_env


class GlobalConfigTestCase(TestCase):
    """Test the `Configuration` class that stores integration settings"""

    def setUp(self):
        self.config = Config()
        self.config.web = IntegrationConfig(self.config, "web")

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
            "<class 'ddtrace.internal.settings._config.Config'> object has no attribute "
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
