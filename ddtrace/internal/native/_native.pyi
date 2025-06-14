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

class StacktraceCollection:
    Disabled: "StacktraceCollection"
    WithoutSymbols: "StacktraceCollection"
    EnabledWithInprocessSymbols: "StacktraceCollection"
    EnabledWithSymbolsInReceiver: "StacktraceCollection"

class CrashtrackerConfiguration:
    def __init__(
        self,
        additional_files: List[str],
        create_alt_stack: bool,
        use_alt_stack: bool,
        timeout_ms: int,
        resolve_frames: StacktraceCollection,
        endpoint: Optional[str],
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

class CrashtrackerMetadata:
    def __init__(self, library_name: str, library_version: str, family: str, tags: Dict[str, str]): ...

class CrashtrackerStatus:
    NotInitialized: "CrashtrackerStatus"
    Initialized: "CrashtrackerStatus"
    FailedToInitialize: "CrashtrackerStatus"

def crashtracker_init(
    config: CrashtrackerConfiguration, receiver_config: CrashtrackerReceiverConfig, metadata: CrashtrackerMetadata
) -> None: ...
def crashtracker_on_fork(
    config: CrashtrackerConfiguration, receiver_config: CrashtrackerReceiverConfig, metadata: CrashtrackerMetadata
) -> None: ...
def crashtracker_status() -> CrashtrackerStatus: ...
def crashtracker_receiver() -> None: ...

class PyTracerMetadata:
    """
    Stores the configuration settings for the Tracer.
    This data is saved in a temporary file while the Tracer is running.
    """

    def __init__(
        self,
        runtime_id: Optional[str],
        tracer_version: str,
        hostname: str,
        service_name: Optional[str],
        service_env: Optional[str],
        service_version: Optional[str],
    ):
        """
        Initialize the `PyTracerMetadata`.
        :param runtime_id: Runtime UUID.
        :param tracer_version: Version of the tracer (e.g., "1.0.0").
        :param hostname: Identifier of the machine running the tracer.
        :param service_name: Name of the service being instrumented.
        :param service_env: Environment of the service being instrumented.
        :param service_version: Version of the service being instrumented.
        """
        ...

class PyAnonymousFileHandle:
    """
    Represents an anonymous file handle.
    On Linux, it uses `memfd` (memory file descriptors) to create temporary files in memory.
    """

    def __init__(self): ...

def store_metadata(data: PyTracerMetadata) -> PyAnonymousFileHandle:
    """
    Create an anonymous file storing the tracer configuration.
    :param data: The tracer configuration to store.
    """
    ...
