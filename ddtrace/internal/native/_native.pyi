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

class TraceExporter:
    """
    TraceExporter is a class responsible for exporting traces to the Agent.
    """

    def __init__(self):
        """
        Initialize a TraceExporter.
        """
        ...
    def send(self, data: bytes, trace_count: int) -> str:
        """
        Send a trace payload to the Agent.
        :param data: The msgpack encoded trace payload to send.
        :param trace_count: The number of traces in the data payload.
        """
        ...
    def shutdown(self, timeout_ns: int) -> None:
        """
        Shutdown the TraceExporter, releasing any resources and ensuring all pending stats are sent.
        This method should be called before the application exits to ensure proper cleanup.
        :param timeout_ns: The maximum time to wait for shutdown in nanoseconds.
        """
        ...
    def drop(self) -> None:
        """
        Drop the TraceExporter, releasing any resources without sending pending stats.
        """
        ...
    def run_worker(self) -> None:
        """
        Start the rust worker threads.
        This starts the runtime required to process rust async tasks including stats and telemetry sending.
        The runtime will also be created when calling `send`,
        this method can be used to start the runtime before sending any traces.
        """
        ...
    def stop_worker(self) -> None:
        """
        Stop the rust worker threads.
        This stops the async runtime and must be called before forking to avoid deadlocks after forking.
        This should be called even if `run_worker` hasn't been called as the runtime will be started
        when calling `send`.
        """
        ...
    def debug(self) -> str:
        """
        Returns a string representation of the exporter.
        Should only be used for debugging.
        """
        ...

class TraceExporterBuilder:
    """
    TraceExporterBuilder is a class responsible for building a TraceExporter.
    """

    def __init__(self):
        """
        Initialize a TraceExporterBuilder.
        """
        ...
    def set_hostname(self, hostname: str) -> TraceExporterBuilder:
        """
        Set the hostname of the TraceExporter.
        :param hostname: The hostname to set for the TraceExporter.
        """
        ...
    def set_url(self, url: str) -> TraceExporterBuilder:
        """
        Set the agent url of the TraceExporter.
        :param url: The URL of the agent to send traces to.
        """
        ...
    def set_dogstatsd_url(self, url: str) -> TraceExporterBuilder:
        """
        Set the DogStatsD URL of the TraceExporter.
        :param url: The URL of the DogStatsD endpoint.
        """
        ...
    def set_env(self, env: str) -> TraceExporterBuilder:
        """
        Set the env of the TraceExporter.
        :param env: The environment name (e.g., 'prod', 'staging', 'dev').
        """
        ...
    def set_app_version(self, version: str) -> TraceExporterBuilder:
        """
        Set the app version of the TraceExporter.
        :param version: The version string of the application.
        """
        ...
    def set_git_commit_sha(self, git_commit_sha: str) -> TraceExporterBuilder:
        """
        Set the git commit sha of the TraceExporter.
        :param git_commit_sha: The git commit SHA of the current code version.
        """
        ...
    def set_tracer_version(self, version: str) -> TraceExporterBuilder:
        """
        Set the tracer version of the TraceExporter.
        :param version: The version string of the tracer.
        """
        ...
    def set_language(self, language: str) -> TraceExporterBuilder:
        """
        Set the language of the TraceExporter.
        :param language: The programming language being traced (e.g., 'python').
        """
        ...
    def set_language_version(self, version: str) -> TraceExporterBuilder:
        """
        Set the language version of the TraceExporter.
        :param version: The version string of the programming language.
        """
        ...
    def set_language_interpreter(self, interpreter: str) -> TraceExporterBuilder:
        """
        Set the language interpreter of the TraceExporter.
        :param vendor: The language interpreter.
        """
        ...
    def set_language_interpreter_vendor(self, vendor: str) -> TraceExporterBuilder:
        """
        Set the language interpreter vendor of the TraceExporter.
        :param vendor: The vendor of the language interpreter.
        """
        ...
    def set_test_session_token(self, token: str) -> TraceExporterBuilder:
        """
        Set the test session token for the TraceExporter.
        :param token: The test session token to use for authentication.
        """
        ...
    def set_input_format(self, input_format: str) -> TraceExporterBuilder:
        """
        Set the input format for the trace data.
        :param input_format: The format to use for input traces (supported values are "v0.4" and "v0.5").
        :raises ValueError: If input_format is not a supported value.
        """
        ...
    def set_output_format(self, output_format: str) -> TraceExporterBuilder:
        """
        Set the output format for the trace data.
        :param output_format: The format to use for output traces (supported values are "v0.4" and "v0.5").
        :raises ValueError: If output_format is not a supported value.
        """
        ...
    def set_client_computed_top_level(self) -> TraceExporterBuilder:
        """
        Set the header indicating the tracer has computed the top-level tag
        """
        ...
    def set_client_computed_stats(self) -> TraceExporterBuilder:
        """
        Set the header indicating the tracer has already computed stats.
        This should not be used along with `enable_stats`.
        The main use is to opt-out trace metrics.
        """
        ...
    def enable_stats(self, bucket_size_ns: int) -> TraceExporterBuilder:
        """
        Enable stats computation in the TraceExporter
        :param bucket_size_ns: The size of stats bucket in nanoseconds.
        """
        ...
    def enable_telemetry(
        self,
        heartbeat_ms: int,
        runtime_id: str,
    ) -> TraceExporterBuilder:
        """
        Emit telemetry in the TraceExporter
        :param heartbeat: The flush interval for telemetry metrics in milliseconds.
        :param runtime_id: The runtime id to use for telemetry.
        """
        ...
    def build(self) -> TraceExporter:
        """
        Build and return a TraceExporter instance with the configured settings.
        This method consumes the builder, so it cannot be used again after calling build.
        :return: A configured TraceExporter instance.
        :raises ValueError: If the builder has already been consumed or if required settings are missing.
        """
        ...
    def debug(self) -> str:
        """
        Returns a string representation of the exporter.
        Should only be used for debugging.
        """
        ...

class AgentError(Exception):
    """
    Raised when there is an error in agent response processing.
    """

    ...

class BuilderError(Exception):
    """
    Raised when there is an error in the TraceExporterBuilder configuration.
    """

    ...

class DeserializationError(Exception):
    """
    Raised when there is an error deserializing trace payload.
    """

    ...

class IoError(Exception):
    """
    Raised when there is an I/O error during trace processing.
    """

    ...

class NetworkError(Exception):
    """
    Raised when there is a network-related error during trace processing.
    """

    ...

class RequestError(Exception):
    """
    Raised when the agent responds with an error code.
    """

    ...

class SerializationError(Exception):
    """
    Raised when there is an error serializing trace payload.
    """

    ...
