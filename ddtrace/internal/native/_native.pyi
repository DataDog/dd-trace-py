from typing import Dict

class DDSketch:
    def __init__(self): ...
    def add(self, value: float) -> None: ...
    def to_proto(self) -> bytes: ...
    @property
    def count(self) -> float: ...

class PyConfigurator:
    """
    PyConfigurator is a class responsible for configuring the Python environment
    for the application. It allows setting environment variables, command-line
    arguments, and file overrides, and retrieving the current configuration.
    """

    def __init__(self, debug_logs: bool):
        """
        Initialize the PyConfigurator.
        :param debug_logs: A boolean indicating whether debug logs should be enabled.
        """
        ...
    def set_local_file_override(self, file: str) -> None:
        """
        Overrides the file path for the configuration. Should not be used outside of tests.
        :param file: The path to the file to override.
        """
        ...
    def get_configuration(self) -> Dict[str, str]:
        """
        Retrieve the on-disk configuration.
        :return: A dictionary containing the current configuration of the form {key: value}.
        """
        ...
