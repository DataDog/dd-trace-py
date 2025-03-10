from typing import Dict, List, Optional

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
        Overrides the local file path for the configuration. Should not be used outside of tests.
        :param file: The path to the local file to override.
        """
        ...
    def set_managed_file_override(self, file: str) -> None:
        """
        Overrides the managed file path for the configuration. Should not be used outside of tests.
        :param file: The path to the managed file to override.
        """
        ...
    def get_configuration(self) -> List[Dict[str, str]]:
        """
        Retrieve the on-disk configuration.
        :return: A list of dictionaries containing the configuration:
            [{"source": ..., "key": ..., "value": ..., "config_id": ...}]
        """
        ...
    @property
    def local_stable_config_type(self) -> str:
        """
        Retrieve the local stable configuration type.
        :return: A string representing the local stable configuration type.
        """
        ...
    @property
    def fleet_stable_config_type(self) -> str:
        """
        Retrieve the fleet stable configuration type.
        :return: A string representing the fleet stable configuration type.
        """
        ...

class CrashtrackerConfiguration:
    def __init__(
        self,
        additional_files: List[str],
        create_alt_stack: bool,
        use_alt_stack: bool,
        timeout_ms: int,
        endpoint: Optional[str],
        resolve_frames: Optional[str],
        unix_socket_path: Optional[str],
    ): ...

class CrashtrackerReceiverConfig:
    def __init__(
        self,
        args: List[str],
        env: Dict[str, str],
        path_to_receiver_binary: str,
        stderr_filename: Optional[str],
        stdout_filename: Optional[str],
    ): ...

class Metadata:
    def __init__(self, library_name: str, library_version: str, family: str, tags: Dict[str, str]): ...

class CrashtrackerStatus: ...

def crashtracker_init(
    config: CrashtrackerConfiguration, receiver_config: CrashtrackerReceiverConfig, metadata: Metadata
) -> None: ...
def crashtracker_on_fork(
    config: CrashtrackerConfiguration, receiver_config: CrashtrackerReceiverConfig, metadata: Metadata
) -> None: ...
def crashtracker_status() -> CrashtrackerStatus: ...
def crashtracker_receiver() -> None: ...
