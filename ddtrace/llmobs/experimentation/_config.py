import os

from .._llmobs import LLMObs
from .utils._exceptions import ConfigurationError


# Default configuration values
DEFAULT_SITE = "datadoghq.com"
DEFAULT_ML_APP = "dne"
MAX_DATASET_ROWS = 50000
MAX_PROGRESS_BAR_WIDTH = 40
DEFAULT_CHUNK_SIZE = 300
DEFAULT_CONCURRENT_JOBS = 10
FLUSH_EVERY = 10

# Global state (initialized by init())
_IS_INITIALIZED = False
_ENV_PROJECT_NAME = None
_ENV_DD_SITE = DEFAULT_SITE
_ENV_DD_API_KEY = None
_ENV_DD_APPLICATION_KEY = None
_ML_APP = DEFAULT_ML_APP
_RUN_LOCALLY = False


# Derived values
def get_api_base_url() -> str:
    """Get the base URL for API requests."""
    if get_site().endswith("datadoghq.com"):
        return f"https://api.{get_site()}"
    elif get_site().endswith("datad0g.com"):
        return "https://dd.datad0g.com"


def get_base_url() -> str:
    """Get the base URL for the LLM Observability UI."""
    if get_site() == "datadoghq.com":
        return "https://app.datadoghq.com"
    elif get_site().endswith("datadoghq.com"):
        return f"https://{get_site()}"
    elif get_site() == "datad0g.com":
        return "https://dd.datad0g.com"


def init(
    project_name: str,
    site: str = None,
    api_key: str = None,
    application_key: str = None,
    run_locally: bool = False,
) -> None:
    """
    Initialize an experiment environment for pushing and retrieving data from Datadog.

    This function configures global settings needed to interact with the Datadog LLM Observability API.
    If 'run_locally' is True, no data will be sent; otherwise, this function ensures that all required
    credentials are available. If no credentials are passed in, it attempts to read them from environment
    variables.

    Args:
        project_name (str): Name of the project to be associated with any experiments.
        site (str, optional): Datadog site domain (e.g., "datadoghq.com"). Defaults to "datadoghq.com".
        api_key (str, optional): Datadog API key. Reads from environment variable DD_API_KEY if None.
        application_key (str, optional): Datadog Application key. Reads from environment variable DD_APPLICATION_KEY if None.
        run_locally (bool, optional): If True, disables communication with Datadog and runs locally.

    Raises:
        ConfigurationError: If required environment variables or parameters are missing when run_locally is False.
    """

    global _IS_INITIALIZED, _ENV_PROJECT_NAME, _ENV_DD_SITE, _ENV_DD_API_KEY, _ENV_DD_APPLICATION_KEY, _RUN_LOCALLY

    if run_locally:
        _RUN_LOCALLY = True

        return

    if api_key is None:
        api_key = os.getenv("DD_API_KEY")
        if api_key is None:
            raise ConfigurationError(
                "DD_API_KEY environment variable is not set, please set it or pass it as an argument to init(...api_key=...)"
            )

    if application_key is None:
        application_key = os.getenv("DD_APPLICATION_KEY")
        if application_key is None:
            raise ConfigurationError(
                "DD_APPLICATION_KEY environment variable is not set, please set it or pass it as an argument to init(...application_key=...)"
            )

    if site is None:
        site = os.getenv("DD_SITE")
        if site is None:
            raise ConfigurationError(
                "DD_SITE environment variable is not set, please set it or pass it as an argument to init(...site=...)"
            )

    if _IS_INITIALIZED is False:
        LLMObs.enable(
            ml_app=_ML_APP,
            integrations_enabled=True,
            agentless_enabled=True,
            site=site,
            api_key=api_key,
        )

    _ENV_PROJECT_NAME = project_name
    _ENV_DD_SITE = site
    _ENV_DD_API_KEY = api_key
    _ENV_DD_APPLICATION_KEY = application_key
    _IS_INITIALIZED = True


def _validate_init() -> None:
    """
    Check if the environment has been initialized.

    Raises:
        ConfigurationError: If the environment is not yet initialized (init() has not been called).
    """
    if _IS_INITIALIZED is False:
        raise ConfigurationError(
            "Environment not initialized, please call ddtrace.llmobs.experiments.init() at the top of your script before calling any other functions"
        )


def _is_locally_initialized() -> bool:
    """
    Check if the environment is configured to run locally.

    Returns:
        bool: True if running locally, False otherwise.
    """
    return _RUN_LOCALLY


def get_api_key() -> str:
    """Get the configured API key."""
    return _ENV_DD_API_KEY


def get_application_key() -> str:
    """Get the configured application key."""
    return _ENV_DD_APPLICATION_KEY


def get_site() -> str:
    """Get the configured site."""
    return _ENV_DD_SITE


def is_initialized() -> bool:
    """Check if the environment has been initialized."""
    return _IS_INITIALIZED


def get_project_name() -> str:
    """Get the configured project name."""
    return _ENV_PROJECT_NAME
