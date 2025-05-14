import os
import ddtrace

from .._llmobs import LLMObs
from .utils._exceptions import ConfigurationError
from ddtrace.internal.utils.formats import asbool


# Default configuration values
MAX_DATASET_ROWS = 50000
MAX_PROGRESS_BAR_WIDTH = 40
DEFAULT_CHUNK_SIZE = 100
DEFAULT_CONCURRENT_JOBS = 10
FLUSH_EVERY = 10
API_PROCESSING_TIME_SLEEP = 6 # Based on events processor median processing time
DEFAULT_ML_APP: str = "dne"
DEFAULT_PROJECT_NAME: str = "Default Project"

# Set of known valid Datadog site domains
VALID_DD_SITES = {
    "datadoghq.com",
    "datadoghq.eu",
    "us3.datadoghq.com",
    "us5.datadoghq.com",
    "ap1.datadoghq.com",
    "datad0g.com", 
}

# Global state (initialized by init())
_IS_INITIALIZED = False
_ENV_PROJECT_NAME = None
_ENV_DD_SITE = None
_ENV_DD_API_KEY = None
_ENV_DD_APPLICATION_KEY = None
_RUN_LOCALLY = False
_ML_APP = None


# Derived values
def get_api_base_url() -> str:
    """Get the base URL for API requests."""
    _validate_init()
    site = get_site()
    if site == "datad0g.com":
        return "https://dd.datad0g.com"
    # Assume standard pattern for all other valid sites (.com, .eu, us3.com, etc.)
    return f"https://api.{site}"


def get_base_url() -> str:
    """Get the base URL for the LLM Observability UI."""
    _validate_init()
    site = get_site()
    if site == "datad0g.com":
        return "https://dd.datad0g.com"
    elif site == "datadoghq.com": 
        return "https://app.datadoghq.com"
    # Assume standard pattern for other valid sites (.eu, us3.com, etc.)
    return f"https://{site}"


def init(
    ml_app: str = DEFAULT_ML_APP,
    project_name: str = DEFAULT_PROJECT_NAME,
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
        ml_app (str): The ML application name.
        project_name (str): Name of the project to be associated with any experiments.
        site (str, optional): Datadog site domain (e.g., "datadoghq.com"). Defaults to "datadoghq.com".
        api_key (str, optional): Datadog API key. Reads from environment variable DD_API_KEY if None.
        application_key (str, optional): Datadog Application key. Reads from environment variable DD_APPLICATION_KEY if None.
        run_locally (bool, optional): If True, disables communication with Datadog and runs locally.

    Raises:
        ConfigurationError: If required environment variables or parameters are missing or invalid when run_locally is False.
    """
    from .utils._ui import Color 

    global _IS_INITIALIZED, _ENV_PROJECT_NAME, _ENV_DD_SITE, _ENV_DD_API_KEY, \
           _ENV_DD_APPLICATION_KEY, _RUN_LOCALLY, _ML_APP

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

    if site not in VALID_DD_SITES:
        raise ConfigurationError(
            f"Invalid Datadog site '{site}' provided. Must be one of: {', '.join(sorted(VALID_DD_SITES))}"
        )


    if not _IS_INITIALIZED:
        trace_enabled_env = os.getenv("DD_TRACE_ENABLED")
        if trace_enabled_env is None:
            os.environ["DD_TRACE_ENABLED"] = "false"
            trace_enabled = False
            print(
                f"{Color.YELLOW}Warning: APM tracing is disabled by default for LLM Experiments. Only LLM Observability tracing is enabled. "
                f"To enable APM tracing, set the DD_TRACE_ENABLED=true environment variable.{Color.RESET}"
            )
        else:
            trace_enabled = asbool(trace_enabled_env)


        LLMObs.enable(
            ml_app=ml_app,
            integrations_enabled=True,
            agentless_enabled=True,
            site=site,
            api_key=api_key,
        )

    _ML_APP = ml_app
    _ENV_PROJECT_NAME = project_name
    _ENV_DD_SITE = site
    _ENV_DD_API_KEY = api_key
    _ENV_DD_APPLICATION_KEY = application_key
    _IS_INITIALIZED = True


def _validate_init() -> None:
    """
    Check if the environment has been initialized for either local or remote operation.

    Raises:
        ConfigurationError: If the environment is not yet initialized (init() has not been called).
    """
    if not _IS_INITIALIZED and not _RUN_LOCALLY:
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
