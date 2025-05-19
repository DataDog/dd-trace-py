import os
from unittest.mock import patch, call

import pytest

import ddtrace.llmobs.experimentation._config as config
from ddtrace.llmobs.experimentation.utils._exceptions import ConfigurationError


# --- Fixture to Reset Global Config State ---

@pytest.fixture(autouse=True)
def reset_config_state():
    """Resets global configuration state before each test."""
    # Reset global state variables to defaults
    config._IS_INITIALIZED = False
    config._ENV_PROJECT_NAME = None
    config._ENV_DD_SITE = None
    config._ENV_DD_API_KEY = None
    config._ENV_DD_APPLICATION_KEY = None
    config._ML_APP = None
    config._RUN_LOCALLY = False

    yield


# --- Test Class ---

class TestExperimentationConfig:
    """Tests for the configuration functions in _config.py."""

    PROJECT_NAME = "TestProject"
    ML_APP = "test_ml_app"  # Add default ML app for tests
    API_KEY = "api123"
    APP_KEY = "app456"
    SITE = "datadoghq.eu"
    DEFAULT_SITE = "datadoghq.com"
    VALID_SITES = ["datadoghq.com", "datadoghq.eu", "us3.datadoghq.com", "datad0g.com"]
    INVALID_SITE = "invalid.site.xyz"

    def test_initial_state(self):
        """Verify the default state before init() is called."""
        assert not config.is_initialized()
        assert not config._is_locally_initialized()
        assert config.get_project_name() is None
        assert config.get_api_key() is None
        assert config.get_application_key() is None
        assert config.get_site() is None
        with pytest.raises(ConfigurationError, match="Environment not initialized"):
            config._validate_init()
        with pytest.raises(ConfigurationError, match="Environment not initialized"):
            config.get_base_url()
        with pytest.raises(ConfigurationError, match="Environment not initialized"):
            config.get_api_base_url()

    def test_init_run_locally(self):
        """Test init() with run_locally=True."""
        with patch("ddtrace.llmobs._llmobs.LLMObs.enable") as mock_llmobs_enable:
            config.init(self.ML_APP, self.PROJECT_NAME, run_locally=True)

            assert not config.is_initialized()
            assert config._is_locally_initialized()
            assert config.get_project_name() is None
            assert config.get_api_key() is None
            assert config.get_application_key() is None
            assert config.get_site() is None

            config._validate_init()

            mock_llmobs_enable.assert_not_called()

    def test_init_with_arguments(self):
        """Test init() with all required arguments provided."""
        with patch("ddtrace.llmobs._llmobs.LLMObs.enable") as mock_llmobs_enable:
            config.init(
                self.ML_APP,
                project_name=self.PROJECT_NAME,
                api_key=self.API_KEY,
                application_key=self.APP_KEY,
                site=self.SITE,
            )

            assert config.is_initialized()
            assert not config._is_locally_initialized()
            assert config.get_project_name() == self.PROJECT_NAME
            assert config.get_api_key() == self.API_KEY
            assert config.get_application_key() == self.APP_KEY
            assert config.get_site() == self.SITE
            config._validate_init()

            mock_llmobs_enable.assert_called_once_with(
                ml_app=self.ML_APP,
                integrations_enabled=True,
                agentless_enabled=True,
                site=self.SITE,
                api_key=self.API_KEY,
            )

    @patch.dict(os.environ, {"DD_API_KEY": API_KEY, "DD_APPLICATION_KEY": APP_KEY, "DD_SITE": SITE}, clear=True)
    def test_init_with_env_vars(self):
        """Test init() reading credentials from environment variables."""
        with patch("ddtrace.llmobs._llmobs.LLMObs.enable") as mock_llmobs_enable:
            config.init(self.ML_APP, self.PROJECT_NAME)

            assert config.is_initialized()
            assert config.get_project_name() == self.PROJECT_NAME
            assert config.get_api_key() == self.API_KEY
            assert config.get_application_key() == self.APP_KEY
            assert config.get_site() == self.SITE
            config._validate_init()

            mock_llmobs_enable.assert_called_once()
            call_args, call_kwargs = mock_llmobs_enable.call_args
            assert call_kwargs.get("site") == self.SITE
            assert call_kwargs.get("api_key") == self.API_KEY

    @patch.dict(os.environ, {"DD_APPLICATION_KEY": APP_KEY, "DD_SITE": SITE}, clear=True)
    def test_init_missing_api_key(self):
        """Test init() raises ConfigurationError if API key is missing."""
        with pytest.raises(ConfigurationError, match="DD_API_KEY .* not set"):
            config.init(self.ML_APP, self.PROJECT_NAME)

    @patch.dict(os.environ, {"DD_API_KEY": API_KEY, "DD_SITE": SITE}, clear=True)
    def test_init_missing_app_key(self):
        """Test init() raises ConfigurationError if Application key is missing."""
        with pytest.raises(ConfigurationError, match="DD_APPLICATION_KEY .* not set"):
            config.init(self.ML_APP, self.PROJECT_NAME)

    @patch.dict(os.environ, {"DD_API_KEY": API_KEY, "DD_APPLICATION_KEY": APP_KEY}, clear=True)
    def test_init_missing_site(self):
        """Test init() raises ConfigurationError if site arg and DD_SITE env var are missing."""
        with pytest.raises(ConfigurationError, match="DD_SITE .* not set"):
            config.init(self.ML_APP, self.PROJECT_NAME)

    @patch.dict(os.environ, {"DD_API_KEY": API_KEY, "DD_APPLICATION_KEY": APP_KEY}, clear=True)
    def test_init_invalid_site_arg(self):
        """Test init() raises ConfigurationError for invalid site argument."""
        with pytest.raises(ConfigurationError, match=f"Invalid Datadog site '{self.INVALID_SITE}'"):
            config.init(
                self.ML_APP,
                self.PROJECT_NAME,
                api_key=self.API_KEY,
                application_key=self.APP_KEY,
                site=self.INVALID_SITE,
            )

    @patch.dict(os.environ, {"DD_API_KEY": API_KEY, "DD_APPLICATION_KEY": APP_KEY, "DD_SITE": INVALID_SITE}, clear=True)
    def test_init_invalid_site_env(self):
        """Test init() raises ConfigurationError for invalid DD_SITE env var."""
        with pytest.raises(ConfigurationError, match=f"Invalid Datadog site '{self.INVALID_SITE}'"):
            config.init(self.ML_APP, self.PROJECT_NAME)

    @pytest.mark.parametrize("valid_site", VALID_SITES)
    def test_init_valid_sites(self, valid_site):
        """Test init() accepts various valid site domains."""
        with patch("ddtrace.llmobs._llmobs.LLMObs.enable") as mock_llmobs_enable:
            config.init(
                self.ML_APP,
                self.PROJECT_NAME,
                api_key=self.API_KEY,
                application_key=self.APP_KEY,
                site=valid_site,
            )
            assert config.is_initialized()
            assert config.get_site() == valid_site
            mock_llmobs_enable.assert_called_once()
            call_args, call_kwargs = mock_llmobs_enable.call_args
            assert call_kwargs.get("site") == valid_site

    def test_init_run_locally_skips_site_validation(self):
        """Test init() with run_locally=True skips site validation."""
        with patch("ddtrace.llmobs._llmobs.LLMObs.enable") as mock_llmobs_enable:
            config.init(self.ML_APP, self.PROJECT_NAME, run_locally=True, site=self.INVALID_SITE)

            assert not config.is_initialized()
            assert config._is_locally_initialized()
            assert config.get_project_name() is None
            assert config.get_api_key() is None
            assert config.get_application_key() is None
            assert config.get_site() is None

            config._validate_init()
            mock_llmobs_enable.assert_not_called()

    @pytest.mark.parametrize(
        "site_input, expected_api_base, expected_ui_base",
        [
            ("datadoghq.com", "https://api.datadoghq.com", "https://app.datadoghq.com"),
            ("us3.datadoghq.com", "https://api.us3.datadoghq.com", "https://us3.datadoghq.com"),
            ("datadoghq.eu", "https://api.datadoghq.eu", "https://datadoghq.eu"),
            ("datad0g.com", "https://dd.datad0g.com", "https://dd.datad0g.com"),
        ],
    )
    def test_url_getters(self, site_input, expected_api_base, expected_ui_base):
        """Test URL generation for different sites."""
        with patch("ddtrace.llmobs._llmobs.LLMObs.enable"):
            config.init(
                self.ML_APP,
                self.PROJECT_NAME,
                api_key="key",
                application_key="key",
                site=site_input,
            )
        assert config.get_api_base_url() == expected_api_base
        assert config.get_base_url() == expected_ui_base

    def test_init_multiple_calls(self):
        """Test calling init() multiple times updates state but enables LLMObs only once."""
        with patch("ddtrace.llmobs._llmobs.LLMObs.enable") as mock_llmobs_enable:
            config.init(
                self.ML_APP,
                self.PROJECT_NAME,
                api_key=self.API_KEY,
                application_key=self.APP_KEY,
                site=self.SITE,
            )
            assert config.is_initialized()
            assert config.get_project_name() == self.PROJECT_NAME
            mock_llmobs_enable.assert_called_once()

            new_project_name = "NewProject"
            second_site = self.DEFAULT_SITE
            config.init(
                self.ML_APP,
                new_project_name,
                api_key="other_api",
                application_key="other_app",
                site=second_site,
            )
            assert config.is_initialized()
            assert config.get_project_name() == new_project_name
            assert config.get_api_key() == "other_api"
            assert config.get_application_key() == "other_app"
            assert config.get_site() == second_site

            mock_llmobs_enable.assert_called_once()

    @patch.dict(os.environ, {"DD_API_KEY": API_KEY, "DD_APPLICATION_KEY": APP_KEY}, clear=True)
    def test_init_missing_site_env_raises_error(self):
        with pytest.raises(ConfigurationError, match="DD_SITE .* not set"):
            config.init(self.ML_APP, self.PROJECT_NAME)

    def test_init_custom_ml_app(self):
        """Test that init accepts a custom ml_app and uses it."""
        custom_ml = "custom_ml_app"
        with patch("ddtrace.llmobs._llmobs.LLMObs.enable") as mock_llmobs_enable:
            config.init(
                custom_ml,
                self.PROJECT_NAME,
                api_key=self.API_KEY,
                application_key=self.APP_KEY,
                site=self.DEFAULT_SITE,
            )

        assert getattr(config, "_ML_APP") == custom_ml

        mock_llmobs_enable.assert_called_once_with(
            ml_app=custom_ml,
            integrations_enabled=True,
            agentless_enabled=True,
            site=self.DEFAULT_SITE,
            api_key=self.API_KEY,
        )

    def test_init_with_default_args_and_env_vars(self):
        """Test init() uses default ml_app/project_name constants when env vars are present."""
        with patch.dict(
            os.environ,
            {"DD_API_KEY": self.API_KEY, "DD_APPLICATION_KEY": self.APP_KEY, "DD_SITE": self.SITE},
            clear=True,
        ):
            with patch("ddtrace.llmobs._llmobs.LLMObs.enable") as mock_llmobs_enable:
                config.init()

                assert config.is_initialized()
                assert getattr(config, "_ML_APP") == config.DEFAULT_ML_APP
                assert config.get_project_name() == config.DEFAULT_PROJECT_NAME

                mock_llmobs_enable.assert_called_once_with(
                    ml_app=config.DEFAULT_ML_APP,
                    integrations_enabled=True,
                    agentless_enabled=True,
                    site=self.SITE,
                    api_key=self.API_KEY,
                )

    def test_init_with_default_args_requires_credentials(self):
        """Test init() with default args still requires credentials."""
        # No env vars set
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ConfigurationError, match="DD_API_KEY .* not set"):
                config.init()

        # Only API key set
        with patch.dict(os.environ, {"DD_API_KEY": self.API_KEY}, clear=True):
            with pytest.raises(ConfigurationError, match="DD_APPLICATION_KEY .* not set"):
                config.init()

        # API key and App key set, but no site
        with patch.dict(
            os.environ,
            {"DD_API_KEY": self.API_KEY, "DD_APPLICATION_KEY": self.APP_KEY},
            clear=True
        ):
            with pytest.raises(ConfigurationError, match="DD_SITE .* not set"):
                config.init()