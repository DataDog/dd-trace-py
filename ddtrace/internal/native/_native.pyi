from typing import Dict, List

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

    def set_input_format(self, format: str) -> TraceExporterBuilder:
        """
        Set the input format for the trace data.
        :param format: The format to use for input traces (supported values are "v0.4" and "v0.5").
        :raises ValueError: If format is not a supported value.
        """
        ...

    def set_output_format(self, format: str) -> TraceExporterBuilder:
        """
        Set the output format for the trace data.
        :param format: The format to use for output traces (supported values are "v0.4" and "v0.5").
        :raises ValueError: If format is not a supported value.
        """
        ...

    def set_client_computed_top_level(self) -> TraceExporterBuilder:
        """
        Set the header indicating the tracer has computed the top-level tag
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
        heartbeat: int,
        runtime_id: str,
    ) -> TraceExporterBuilder:
        """
        Enable stats computation in the TraceExporter
        :param bucket_size_ns: The size of stats bucket in nanoseconds.
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

class EncodingNotSupportedError(Exception):
    """
    Raised when the agent return a 404 or 415 error code, indicating the encoding format is not supported.
    """
