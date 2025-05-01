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
    config._ENV_DD_SITE = config.DEFAULT_SITE
    config._ENV_DD_API_KEY = None
    config._ENV_DD_APPLICATION_KEY = None
    config._ML_APP = config.DEFAULT_ML_APP
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
    VALID_SITES = ["datadoghq.com", "datadoghq.eu", "us3.datadoghq.com", "datad0g.com"]
    INVALID_SITE = "invalid.site.xyz"

    def test_initial_state(self):
        """Verify the default state before init() is called."""
        assert not config.is_initialized()
        assert not config._is_locally_initialized()
        assert config.get_project_name() is None
        assert config.get_api_key() is None
        assert config.get_application_key() is None
        assert config.get_site() == config.DEFAULT_SITE
        # _validate_init should raise before init is called
        with pytest.raises(ConfigurationError, match="Environment not initialized"):
            config._validate_init()
        # Check default URLs
        assert config.get_base_url() == "https://app.datadoghq.com"
        assert config.get_api_base_url() == "https://api.datadoghq.com"

    def test_init_run_locally(self):
        """Test init() with run_locally=True."""
        with patch("ddtrace.llmobs._llmobs.LLMObs.enable") as mock_llmobs_enable:
            config.init(self.ML_APP, self.PROJECT_NAME, run_locally=True)

            assert not config.is_initialized() # is_initialized tracks remote init state
            assert config._is_locally_initialized()
            assert config.get_project_name() is None # Project name is not used in local mode
            assert config.get_api_key() is None
            assert config.get_application_key() is None
            assert config.get_site() == config.DEFAULT_SITE

            # _validate_init should PASS when running locally
            config._validate_init()

            mock_llmobs_enable.assert_not_called() # LLMObs should not be enabled

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

            # Check LLMObs was enabled correctly with the provided args
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
            # Pass only project_name, expect others to come from mocked os.environ
            config.init(self.ML_APP, self.PROJECT_NAME)

            assert config.is_initialized()
            assert config.get_project_name() == self.PROJECT_NAME
            assert config.get_api_key() == self.API_KEY
            assert config.get_application_key() == self.APP_KEY
            assert config.get_site() == self.SITE
            config._validate_init()

            mock_llmobs_enable.assert_called_once()
            # Check args used in the call match the environment variables
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
        """Test init() uses default site if site arg and DD_SITE env var are missing."""
        # This test previously expected an error, but now we expect fallback to default.
        with patch("ddtrace.llmobs._llmobs.LLMObs.enable") as mock_llmobs_enable:
            # No site arg, no DD_SITE env var set in patch.dict
            config.init(self.ML_APP, self.PROJECT_NAME)

            assert config.is_initialized()
            assert config.get_site() == config.DEFAULT_SITE
            config._validate_init()
            mock_llmobs_enable.assert_called_once()
            call_args, call_kwargs = mock_llmobs_enable.call_args
            # Check default site was used
            assert call_kwargs.get("site") == config.DEFAULT_SITE

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
            # Reads invalid site from env
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
         # Mock LLMObs.enable to ensure it's NOT called
        with patch("ddtrace.llmobs._llmobs.LLMObs.enable") as mock_llmobs_enable:
            # Use an invalid site; shouldn't matter due to run_locally=True
            config.init(self.ML_APP, self.PROJECT_NAME, run_locally=True, site=self.INVALID_SITE)

            assert not config.is_initialized()
            assert config._is_locally_initialized()
            assert config.get_project_name() is None # Project name should NOT be set locally
            # Other credentials/site should remain unset/default when run_locally=True
            assert config.get_api_key() is None
            assert config.get_application_key() is None
            assert config.get_site() == config.DEFAULT_SITE

            config._validate_init()
            mock_llmobs_enable.assert_not_called()

    @pytest.mark.parametrize(
        "site_input, expected_api_base, expected_ui_base",
        [
            ("datadoghq.com", "https://api.datadoghq.com", "https://app.datadoghq.com"),
            ("us3.datadoghq.com", "https://api.us3.datadoghq.com", "https://us3.datadoghq.com"),
            ("datadoghq.eu", "https://api.datadoghq.eu", "https://datadoghq.eu"),
            ("datad0g.com", "https://dd.datad0g.com", "https://dd.datad0g.com"), # Staging/Test
        ],
    )
    def test_url_getters(self, site_input, expected_api_base, expected_ui_base):
        """Test URL generation for different sites."""
         # Need to init to set the site for the getters
        with patch("ddtrace.llmobs._llmobs.LLMObs.enable"): # Mock enable to avoid actual call
             # Use known keys to pass init validation
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
            # First call
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

            # Second call with different project name and another valid site
            new_project_name = "NewProject"
            # Use a valid site (e.g., the default) to avoid site validation errors
            second_site = config.DEFAULT_SITE
            config.init(
                self.ML_APP,
                new_project_name,
                # API/App key changes should be ignored by LLMObs.enable on subsequent calls
                api_key="other_api",
                application_key="other_app",
                site=second_site,
            )
            assert config.is_initialized()
            assert config.get_project_name() == new_project_name
            # Keys and site should reflect the *second* call for getters
            assert config.get_api_key() == "other_api"
            assert config.get_application_key() == "other_app"
            assert config.get_site() == second_site

            # Crucially, LLMObs.enable should still only have been called once
            mock_llmobs_enable.assert_called_once()

    @patch.dict(os.environ, {"DD_API_KEY": API_KEY, "DD_APPLICATION_KEY": APP_KEY}, clear=True)
    def test_init_missing_site_env_uses_default(self):
        """Test init() uses default site if DD_SITE env var is missing."""
        with patch("ddtrace.llmobs._llmobs.LLMObs.enable") as mock_llmobs_enable:
            # No site arg provided, and DD_SITE env var is not set in patch.dict
            config.init(self.ML_APP, self.PROJECT_NAME)

            assert config.is_initialized()
            assert config.get_site() == config.DEFAULT_SITE
            mock_llmobs_enable.assert_called_once()
            call_args, call_kwargs = mock_llmobs_enable.call_args
            assert call_kwargs.get("site") == config.DEFAULT_SITE

    def test_init_custom_ml_app(self):
        """Test that init accepts a custom ml_app and uses it."""
        custom_ml = "custom_ml_app"
        with patch("ddtrace.llmobs._llmobs.LLMObs.enable") as mock_llmobs_enable:
            # only project_name is required; ml_app comes first
            config.init(custom_ml, self.PROJECT_NAME, api_key=self.API_KEY, application_key=self.APP_KEY)

        # Internal ML app name should be updated
        assert getattr(config, "_ML_APP") == custom_ml

        # LLMObs.enable should be called with the custom ml_app
        mock_llmobs_enable.assert_called_once_with(
            ml_app=custom_ml,
            integrations_enabled=True,
            agentless_enabled=True,
            site=config.DEFAULT_SITE,
            api_key=self.API_KEY,
        )

    def test_init_missing_required_args(self):
        """Test init() raises TypeError when missing required arguments."""
        with pytest.raises(TypeError):
            config.init(project_name=self.PROJECT_NAME)  # Missing ml_app

        with pytest.raises(TypeError):
            config.init(self.ML_APP)  # Missing project_name