from typing import Optional


class DatasetFileError(Exception):
    """
    Exception raised when there are errors reading or processing dataset files,
    such as CSV or JSON errors or file permission issues.
    """

    pass


class ExperimentTaskError(Exception):
    """
    Exception raised when a task fails during experiment execution.

    Attributes:
        message (str): Error message describing the failure.
        row_idx (int): The index of the dataset record on which the task failed.
        original_error (Exception): The original error object (if any).
    """

    def __init__(self, message: str, row_idx: int, original_error: Optional[Exception] = None) -> None:
        self.row_idx = row_idx
        self.original_error = original_error
        super().__init__(message)


class DatadogAPIError(Exception):
    """
    Base exception for all Datadog API errors.

    Attributes:
        status_code (int): The HTTP status code returned by the API.
        message (str): Error message describing the failure.
    """

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        self.message = message
        super().__init__(f"Request failed with status code {status_code}: {message}")


class ConfigurationError(Exception):
    """
    Exception raised when there are errors in configuration settings,
    such as missing API keys, invalid site configurations, or
    uninitialized environment.
    """

    pass


class DatadogAuthenticationError(DatadogAPIError):
    """
    Exception raised for authentication failures (typically status code 403).
    Indicates issues with API keys, application keys, or site configuration.
    """

    pass
