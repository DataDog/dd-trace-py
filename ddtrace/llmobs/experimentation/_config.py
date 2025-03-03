import os

from .._llmobs import LLMObs


# Default configuration values
DEFAULT_SITE = "datadoghq.com"
DEFAULT_ML_APP = "dne"
MAX_DATASET_ROWS = 50000
MAX_PROGRESS_BAR_WIDTH = 40
DEFAULT_CHUNK_SIZE = 300
DEFAULT_CONCURRENT_JOBS = 10

# Global state (initialized by init())
IS_INITIALIZED = False
ENV_PROJECT_NAME = None
ENV_DD_SITE = DEFAULT_SITE
ENV_DD_API_KEY = None
ENV_DD_APPLICATION_KEY = None
ML_APP = DEFAULT_ML_APP
RUN_LOCALLY = False

# Derived values
BASE_URL = f"https://api.{ENV_DD_SITE}"


def init(
    project_name: str,
    site: str = DEFAULT_SITE,
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
        ValueError: If required environment variables or parameters are missing when run_locally is False.
    """

    global IS_INITIALIZED, ENV_PROJECT_NAME, ENV_DD_SITE, ENV_DD_API_KEY, ENV_DD_APPLICATION_KEY, RUN_LOCALLY

    if run_locally:
        RUN_LOCALLY = True

        return

    if api_key is None:
        api_key = os.getenv("DD_API_KEY")
        if api_key is None:
            raise ValueError(
                "DD_API_KEY environment variable is not set, please set it or pass it as an argument to init(...api_key=...)"
            )

    if application_key is None:
        application_key = os.getenv("DD_APPLICATION_KEY")
        if application_key is None:
            raise ValueError(
                "DD_APPLICATION_KEY environment variable is not set, please set it or pass it as an argument to init(...application_key=...)"
            )

    if site is None:
        site = os.getenv("DD_SITE")
        if site is None:
            raise ValueError(
                "DD_SITE environment variable is not set, please set it or pass it as an argument to init(...site=...)"
            )

    if IS_INITIALIZED is False:
        LLMObs.enable(
            ml_app=ML_APP,
            integrations_enabled=True,
            agentless_enabled=True,
            site=site,
            api_key=api_key,
        )

    ENV_PROJECT_NAME = project_name
    ENV_DD_SITE = site
    ENV_DD_API_KEY = api_key
    ENV_DD_APPLICATION_KEY = application_key
    IS_INITIALIZED = True


def _validate_init() -> None:
    """
    Check if the environment has been initialized.

    Raises:
        ValueError: If the environment is not yet initialized (init() has not been called).
    """
    if IS_INITIALIZED is False:
        raise ValueError(
            "Environment not initialized, please call ddtrace.llmobs.experiments.init() at the top of your script before calling any other functions"
        )


def _is_locally_initialized() -> bool:
    """
    Check if the environment is configured to run locally.

    Returns:
        bool: True if running locally, False otherwise.
    """
    return RUN_LOCALLY

