import os
from unittest import TestCase

import pytest

from ddtrace import config as global_config
from ddtrace.internal.settings._agentless import AgentlessConfig
from ddtrace.internal.settings._config import Config
from ddtrace.internal.settings._config import _integration_default_service_names_from_config
from ddtrace.internal.settings.integration import IntegrationConfig

from ..utils import override_env


class GlobalConfigTestCase(TestCase):
    """Test the `Configuration` class that stores integration settings"""

    def setUp(self):
        self.config = Config()
        self.config.web = IntegrationConfig(self.config, "web")

    def test_integration_default_service_names_from_config(self):
        ic = IntegrationConfig(self.config, "celery")
        ic["_default_service_worker"] = "worker-svc"
        ic["_default_service_producer"] = "producer-svc"
        assert _integration_default_service_names_from_config(ic) == {"worker-svc", "producer-svc"}

    def test_integration_default_services_updates_on_singleton_add(self):
        from ddtrace.internal.settings import _config as cfg_mod

        unique = "ddtrace-global-config-test-default-service-name"
        cfg_mod.config._add("structlog", {"_default_service": unique})
        assert unique in cfg_mod.config._integration_default_services

    def test_integration_default_services_updates_on_instance_add(self):
        unique = "ddtrace-isolated-config-test-default-service-name"
        assert unique not in self.config._integration_default_services
        self.config._add("structlog", {"_default_service": unique})
        assert unique in self.config._integration_default_services

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


def test_raise_property_bridges_to_native():
    from ddtrace import config
    from ddtrace.internal.native import config as native_config

    original = config._raise
    try:
        config._raise = True
        assert native_config.get_raise() is True
        assert config._raise is True

        native_config.set_raise(False)
        assert config._raise is False
    finally:
        config._raise = original


def test_new_config_does_not_change_native_raise():
    from ddtrace.internal.native import config as native_config

    original = native_config.get_raise()
    try:
        native_config.set_raise(True)
        with override_env(dict(), replace_os_env=True):
            Config()

        assert native_config.get_raise() is True
    finally:
        native_config.set_raise(original)


def test_agentless_enabled_requires_an_api_key():
    with override_env(dict(DD_AGENTLESS_ENABLED="true"), replace_os_env=True):
        with pytest.raises(ValueError, match="DD_API_KEY"):
            AgentlessConfig()


def test_agentless_enabled_requires_an_api_key_at_import(run_python_code_in_subprocess):
    """The api key check has to fail the process, not just AgentlessConfig()."""
    env = os.environ.copy()
    env["DD_AGENTLESS_ENABLED"] = "true"
    env.pop("DD_API_KEY", None)

    _, stderr, status, _ = run_python_code_in_subprocess("import ddtrace", env=env)

    assert status != 0
    assert b"DD_AGENTLESS_ENABLED is set but DD_API_KEY is not" in stderr


def test_agentless_enabled_is_the_default_for_every_product():
    with override_env(dict(DD_AGENTLESS_ENABLED="true", DD_API_KEY="foobar"), replace_os_env=True):
        c = AgentlessConfig()

    assert c.enabled is True
    assert c.apm_tracing is True
    assert c.llmobs is True
    assert c.ci_visibility is True
    assert c.any_enabled is True


def test_agentless_disabled_by_default():
    with override_env(dict(DD_API_KEY="foobar"), replace_os_env=True):
        c = AgentlessConfig()

    assert c.enabled is False
    assert c.apm_tracing is False
    # Left unset, LLM Observability decides at startup instead of here.
    assert c.llmobs is None
    assert c.ci_visibility is False
    assert c.any_enabled is False


def test_product_agentless_setting_overrides_the_global_one():
    with override_env(
        dict(
            DD_AGENTLESS_ENABLED="true",
            DD_API_KEY="foobar",
            _DD_APM_TRACING_AGENTLESS_ENABLED="false",
            DD_LLMOBS_AGENTLESS_ENABLED="false",
        ),
        replace_os_env=True,
    ):
        c = AgentlessConfig()

    assert c.enabled is True
    assert c.apm_tracing is False
    assert c.llmobs is False
    # Untouched products still follow the global setting.
    assert c.ci_visibility is True


def test_a_product_setting_alone_turns_agentless_on():
    """Products can opt in individually, without the global switch."""
    with override_env(dict(DD_API_KEY="foobar", DD_CIVISIBILITY_AGENTLESS_ENABLED="true"), replace_os_env=True):
        c = AgentlessConfig()

    assert c.enabled is False
    assert c.ci_visibility is True
    assert c.apm_tracing is False
    assert c.any_enabled is True


def test_config_exposes_what_agentless_resolves():
    """ddtrace.config must surface AgentlessConfig's answers rather than deriving its own.

    Both are built from the same environment here on purpose: comparing a fresh Config against the
    import-time singleton would instead measure whichever neighbouring test last touched os.environ.
    """
    env = dict(
        DD_API_KEY="foobar",
        DD_SITE="datad0g.com",
        DD_AGENTLESS_ENABLED="true",
        DD_CIVISIBILITY_AGENTLESS_ENABLED="false",
    )
    with override_env(env, replace_os_env=True):
        agentless = AgentlessConfig()
        c = Config()

    assert c._agentless_enabled is agentless.enabled
    assert c._trace_agentless_enabled is agentless.apm_tracing
    assert c._llmobs_agentless_enabled is agentless.llmobs
    assert c._ci_visibility_agentless_enabled is agentless.ci_visibility
    assert c._dd_site == agentless.site
    assert c._dd_api_key == agentless.api_key
    # ...and the override in that environment is genuinely reflected, not just self-consistent.
    assert c._agentless_enabled is True
    assert c._ci_visibility_agentless_enabled is False
