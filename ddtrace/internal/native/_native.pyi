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

    def send(self, data: bytes) -> None:
        """
        Send a trace payload to the Agent.
        :param data: The msgpack encoded trace payload to send.
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

    def set_hostname(self, hostname: str) -> None:
        """
        Set the hostname of the TraceExporter.
        :param hostname: The hostname to set for the TraceExporter.
        """
        ...

    def set_url(self, url: str) -> None:
        """
        Set the agent url of the TraceExporter.
        :param url: The URL of the agent to send traces to.
        """
        ...

    def set_dogstatsd_url(self, url: str) -> None:
        """
        Set the DogStatsD URL of the TraceExporter.
        :param url: The URL of the DogStatsD endpoint.
        """
        ...

    def set_env(self, env: str) -> None:
        """
        Set the env of the TraceExporter.
        :param env: The environment name (e.g., 'prod', 'staging', 'dev').
        """
        ...

    def set_app_version(self, version: str) -> None:
        """
        Set the app version of the TraceExporter.
        :param version: The version string of the application.
        """
        ...

    def set_git_commit_sha(self, git_commit_sha: str) -> None:
        """
        Set the git commit sha of the TraceExporter.
        :param git_commit_sha: The git commit SHA of the current code version.
        """
        ...

    def set_tracer_version(self, version: str) -> None:
        """
        Set the tracer version of the TraceExporter.
        :param version: The version string of the tracer.
        """
        ...

    def set_language(self, language: str) -> None:
        """
        Set the language of the TraceExporter.
        :param language: The programming language being traced (e.g., 'python').
        """
        ...

    def set_language_version(self, version: str) -> None:
        """
        Set the language version of the TraceExporter.
        :param version: The version string of the programming language.
        """
        ...

    def set_language_interpreter_vendor(self, vendor: str) -> None:
        """
        Set the language interpreter vendor of the TraceExporter.
        :param vendor: The vendor of the language interpreter (e.g., 'CPython').
        """
        ...

    def set_input_format(self, format: TraceFormat) -> None:
        """
        Set the input format for the trace data.
        :param format: The format to use for input traces (v04 or v05).
        """
        ...

    def set_output_format(self, format: TraceFormat) -> None:
        """
        Set the output format for the trace data.
        :param format: The format to use for output traces (v04 or v05).
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

class TraceFormat:
    """
    TraceFormat is an enum of possible formats for traces.
    """

    V04 = "v04"
    V05 = "v05"
