from enum import Enum
from typing import Any
from typing import Iterable
from typing import Iterator
from typing import Literal
from typing import Mapping
from typing import Optional
from typing import TypeVar
from typing import Union

from ddtrace._trace.types import _AttributeValueType

_SpanDataT = TypeVar("_SpanDataT", bound="SpanData")

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
    def get_configuration(self) -> list[dict[str, str]]:
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
        additional_files: list[str],
        create_alt_stack: bool,
        use_alt_stack: bool,
        timeout_ms: int,
        resolve_frames: StacktraceCollection,
        collect_all_threads: bool,
        max_threads: int,
        endpoint: Optional[str] = None,
        unix_socket_path: Optional[str] = None,
        test_token: Optional[str] = None,
    ): ...

class CrashtrackerReceiverConfig:
    def __init__(
        self,
        args: list[str],
        env: dict[str, str],
        path_to_receiver_binary: str,
        stderr_filename: Optional[str],
        stdout_filename: Optional[str],
    ): ...

class CrashtrackerMetadata:
    def __init__(self, library_name: str, library_version: str, family: str, tags: dict[str, str]): ...

class CrashtrackerStatus:
    NotInitialized: "CrashtrackerStatus"
    Initialized: "CrashtrackerStatus"
    FailedToInitialize: "CrashtrackerStatus"

def crashtracker_init(
    config: CrashtrackerConfiguration,
    receiver_config: CrashtrackerReceiverConfig,
    metadata: CrashtrackerMetadata,
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
        process_tags: Optional[str],
        container_id: Optional[str],
    ):
        """
        Initialize the `PyTracerMetadata`.
        :param runtime_id: Runtime UUID.
        :param tracer_version: Version of the tracer (e.g., "1.0.0").
        :param hostname: Identifier of the machine running the tracer.
        :param service_name: Name of the service being instrumented.
        :param service_env: Environment of the service being instrumented.
        :param service_version: Version of the service being instrumented.
        :param process_tags: Process tags of the application being instrumented.
        :param container_id: Container id seen by the application.
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

class SharedRuntime:
    """
    SharedRuntime manages a shared Tokio async runtime used by TraceExporter instances.
    It provides fork-safety hooks to pause and resume the runtime around process forks.
    """

    def __init__(self) -> None: ...
    def before_fork(self) -> None:
        """Prepare the shared runtime for forking. Call this before os.fork()."""
        ...
    def after_fork_parent(self) -> None:
        """Resume the shared runtime in the parent process after forking."""
        ...
    def after_fork_child(self) -> None:
        """Re-initialize the shared runtime in the child process after forking."""
        ...
    def shutdown(self, timeout_ms: Optional[int] = None) -> None:
        """Gracefully shut down the shared runtime.

        Args:
            timeout_ms: Maximum time in milliseconds to wait for shutdown.
                If None, waits indefinitely.
        """
        ...
    def debug(self) -> str:
        """Returns a string representation of the runtime. Should only be used for debugging."""
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
    def send(self, data: bytes) -> str:
        """
        Send a trace payload to the Agent.
        :param data: The msgpack encoded trace payload to send.
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
    def set_service(self, service: str) -> TraceExporterBuilder:
        """
        Set the service name of the TraceExporter.
        :param version: The version string of the application.
        """
        ...
    def set_git_commit_sha(self, git_commit_sha: str) -> TraceExporterBuilder:
        """
        Set the git commit sha of the TraceExporter.
        :param git_commit_sha: The git commit SHA of the current code version.
        """
        ...
    def set_process_tags(self, process_tags: str) -> TraceExporterBuilder:
        """
        Set the process tags to be included in the stats payload.
        :param process_tags: Comma-separated list of key:value process tags (e.g., "key1:val1,key2:val2").
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

    def enable_client_side_stats_obfuscation(self) -> TraceExporterBuilder:
        """
        Obfuscate client side stats buckets in the client instead of in the agent.
        """
        ...
    def enable_telemetry(
        self,
        heartbeat_ms: int,
        runtime_id: str,
        debug_enabled: bool,
    ) -> TraceExporterBuilder:
        """
        Emit telemetry in the TraceExporter
        :param heartbeat: The flush interval for telemetry metrics in milliseconds.
        :param runtime_id: The runtime id to use for telemetry.
        :param debug_enabled: Whether to enable debug logging for telemetry.
        """
        ...
    def enable_health_metrics(self) -> TraceExporterBuilder:
        """
        Enable health metrics in the TraceExporter
        """
        ...
    def set_otlp_endpoint(self, url: str) -> TraceExporterBuilder:
        """
        Set the OTLP HTTP/JSON endpoint for trace export.
        When set, traces are sent to this endpoint instead of the Datadog agent.
        The host language is responsible for resolving the endpoint from its own
        configuration (e.g. OTEL_EXPORTER_OTLP_TRACES_ENDPOINT).
        :param url: The full URL of the OTLP endpoint (e.g. "http://localhost:4318/v1/traces").
        """
        ...
    def set_otlp_headers(self, headers: list[tuple[str, str]]) -> TraceExporterBuilder:
        """
        Set additional HTTP headers for OTLP trace export requests.
        :param headers: A list of (key, value) header pairs.
        """
        ...
    def set_connection_timeout(self, timeout_ms: int) -> TraceExporterBuilder:
        """
        Set the connection timeout in milliseconds for trace export requests.
        :param timeout_ms: Timeout in milliseconds.
        """
        ...
    def build(self, shared_runtime: SharedRuntime) -> TraceExporter:
        """
        Build and return a TraceExporter instance with the configured settings.
        This method consumes the builder, so it cannot be used again after calling build.
        :param shared_runtime: A SharedRuntime instance to share with this exporter.
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

class AgentResponse:
    """Sampling-rate response from the Datadog agent after a successful trace export."""

    rate_by_service: Mapping[str, float]

    def __init__(self, rate_by_service: Mapping[str, float]) -> None: ...

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

class SharedRuntimeError(Exception):
    """
    Raised when there is an error in the SharedRuntime lifecycle (fork hooks, shutdown, etc.).
    """

    ...

class logger:
    """
    Native logging module for configuring and managing log output.
    """

    @staticmethod
    def configure(
        output: Literal["stdout", "stderr", "file"] = "stdout",
        path: Optional[str] = None,
        max_files: Optional[int] = None,
        max_size_bytes: Optional[int] = None,
    ) -> None:
        """
        Configure the logger with the specified output destination.

        :param output: Output destination ("stdout", "stderr", or "file")
        :param path: File path (required if output is "file")
        :param max_files: Maximum number of log files to keep (for file output)
        :param max_size_bytes: Maximum size of each log file in bytes (for file output)
        :raises ValueError: If configuration is invalid
        """
        ...
    @staticmethod
    def disable(output: str) -> None:
        """
        Disable logging output by type.

        :param output: Output type to disable ("file", "stdout", or "stderr")
        :raises ValueError: If output type is invalid
        """
        ...
    @staticmethod
    def set_log_level(level: str) -> None:
        """
        Set the log level for the logger.

        :param level: Log level ("trace", "debug", "info", "warning", or "error")
        :raises ValueError: If log level is invalid
        """
        ...
    @staticmethod
    def log(level: str, message: str) -> None:
        """
        Logs messages

        :param level: Log level ("trace", "debug", "info", "warn", or "error")
        :param message: message to be displayed in the log.
        :raises ValueError: If log level is invalid
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

class ffe:
    """
    Native Feature Flags and Experimentation module.
    """

    class FlagType(Enum):
        String = ...
        Integer = ...
        Float = ...
        Boolean = ...
        Object = ...

    class Reason(Enum):
        Static = ...
        Default = ...
        TargetingMatch = ...
        Split = ...
        Cached = ...
        Disabled = ...
        Unknown = ...
        Stale = ...
        Error = ...

    class ErrorCode(Enum):
        TypeMismatch = ...
        ParseError = ...
        FlagNotFound = ...
        TargetingKeyMissing = ...
        InvalidContext = ...
        ProviderNotReady = ...
        General = ...

    class ResolutionDetails:
        @property
        def value(self) -> Optional[Any]: ...
        @property
        def error_code(self) -> Optional[ffe.ErrorCode]: ...
        @property
        def error_message(self) -> Optional[str]: ...
        @property
        def reason(self) -> Optional[ffe.Reason]: ...
        @property
        def variant(self) -> Optional[str]: ...
        @property
        def allocation_key(self) -> Optional[str]: ...
        @property
        def flag_metadata(self) -> dict[str, str]: ...
        @property
        def do_log(self) -> bool: ...

    class Configuration:
        def __init__(self, config_bytes: bytes) -> None: ...
        def resolve_value(self, flag_key: str, expected_type: ffe.FlagType, context: dict) -> ffe.ResolutionDetails: ...

class native_flare:
    class ListeningError(Exception): ...
    class LockError(Exception): ...
    class ParsingError(Exception): ...
    class SendError(Exception): ...
    class ZipError(Exception): ...

    class FlareAction:
        def __repr__(self) -> str: ...
        def is_send(self) -> bool: ...
        def is_set(self) -> bool: ...
        def is_unset(self) -> bool: ...
        @property
        def level(self) -> Optional[str]: ...
        @property
        def case_id(self) -> Optional[str]: ...
        @staticmethod
        def none_action() -> native_flare.FlareAction: ...

    class TracerFlareManager:
        def __init__(self, agent_url: str) -> None: ...
        def handle_remote_config_data(self, data: Any, product: str) -> native_flare.FlareAction: ...
        def zip_and_send(self, directory: str, send_action: native_flare.FlareAction) -> None: ...
        def set_current_log_level(self, level: str) -> None: ...

class SpanData:
    name: str
    service: Optional[str]
    resource: str
    span_type: Optional[str]
    start_ns: int
    duration_ns: Optional[int]  # None when not set (duration == -1 sentinel)
    error: int
    span_id: int
    trace_id: int
    _trace_id_64bits: int
    start: float  # Convenience property: start_ns / 1e9 (in seconds)
    duration: Optional[float]  # Convenience property: duration_ns / 1e9 (in seconds)
    parent_id: Optional[int]  # TODO[5.0.0] change type to `int`
    _span_api: str

    def __new__(
        cls: type[_SpanDataT],
        name: str,
        service: Optional[str] = None,
        resource: Optional[str] = None,
        span_type: Optional[str] = None,
        trace_id: Optional[int] = None,
        span_id: Optional[int] = None,
        parent_id: Optional[int] = None,
        start: Optional[float] = None,
        context: Optional[Any] = None,  # placeholder for Span.__init__
        on_finish: Optional[Any] = None,  # placeholder for Span.__init__
        span_api: Optional[str] = None,
        links: Optional[list[SpanLink]] = None,  # placeholder for Span.__init__
    ) -> _SpanDataT: ...
    @property
    def finished(self) -> bool: ...  # Read-only, returns duration_ns != -1
    def _set_struct_tag(self, key: str, value: dict[str, Any]) -> None: ...
    def _get_struct_tag(self, key: str) -> Optional[dict[str, Any]]: ...
    def _remove_struct_tag(self, key: str) -> Optional[dict[str, Any]]: ...
    def _has_meta_structs(self) -> bool: ...
    def _get_meta_structs(self) -> dict[str, Any]: ...
    def _set_link(
        self,
        trace_id: int,
        span_id: int,
        tracestate: Optional[str] = None,
        flags: Optional[int] = None,
        attributes: Optional[Mapping[str, _AttributeValueType]] = None,
    ) -> None: ...
    def _add_event(
        self,
        name: str,
        attributes: Optional[Mapping[str, _AttributeValueType]] = None,
        time_unix_nano: Optional[int] = None,
    ) -> None: ...
    def _get_links(self) -> list["SpanLink"]: ...
    def _get_events(self) -> list["SpanEvent"]: ...
    def _has_links(self) -> bool: ...
    def _has_events(self) -> bool: ...

    # Attribute API
    def _set_attribute(self, key: str, value: Union[str, int, float]) -> None: ...
    def _set_attributes(self, attrs: dict[str, Union[str, int, float]]) -> None: ...
    def _has_attribute(self, key: str) -> bool: ...
    def _remove_attribute(self, key: str) -> None: ...
    def _get_attribute(self, key: str) -> Optional[Union[str, int, float]]: ...
    def _get_str_attribute(self, key: str) -> Optional[str]: ...
    def _get_numeric_attribute(self, key: str) -> Optional[Union[int, float]]: ...
    def _get_attributes(self) -> Mapping[str, Union[str, int, float]]: ...
    def _get_str_attributes(self) -> Mapping[str, str]: ...
    def _get_numeric_attributes(self) -> Mapping[str, Union[int, float]]: ...
    def _set_default_attributes(self, values: Mapping[str, Union[str, int, float]]) -> None: ...

class SpanEvent:
    name: str
    time_unix_nano: int  # u64 in Rust; always non-negative
    attributes: dict[str, Any]
    def __init__(
        self,
        name: str,
        attributes: Optional[Mapping[str, _AttributeValueType]] = None,
        time_unix_nano: Optional[int] = None,
    ): ...
    def __repr__(self) -> str: ...
    def __iter__(self) -> Iterator[tuple[str, Any]]: ...
    def __reduce__(self) -> tuple: ...

class SpanLink:
    trace_id: int
    span_id: int
    tracestate: Optional[str]
    flags: Optional[int]
    attributes: dict[str, Any]

    def __init__(
        self,
        trace_id: int,
        span_id: int,
        tracestate: Optional[str] = None,
        flags: Optional[int] = None,
        attributes: Optional[Mapping[str, _AttributeValueType]] = None,
        _skip_validation: bool = False,
    ) -> None: ...
    def to_dict(self) -> dict[str, Any]: ...
    def __eq__(self, other: object) -> bool: ...
    def __repr__(self) -> str: ...
    def __reduce__(self) -> tuple: ...

class ResultType:
    value: int
    name: str
    RESULT_OK: "ResultType"
    RESULT_EXCEPTION: "ResultType"
    RESULT_UNDEFINED: "ResultType"
    def __eq__(self, other: object) -> bool: ...
    def __hash__(self) -> int: ...
    def __repr__(self) -> str: ...
    def __int__(self) -> int: ...

class EventResult:
    response_type: Any
    value: Any
    exception: Any
    def __init__(
        self,
        response_type: Any = None,
        value: Any = None,
        exception: Any = None,
    ) -> None: ...
    def __bool__(self) -> bool: ...
    def __repr__(self) -> str: ...

class EventResultDict(dict):
    def __missing__(self, key: Any) -> EventResult: ...
    def __getattr__(self, name: str) -> EventResult: ...

def has_listeners(event_id: str) -> bool: ...
def on(event_id: str, callback: Any, name: Any = None) -> None: ...
def reset(event_id: Optional[str] = None, callback: Optional[Any] = None) -> None: ...
def dispatch(event_id: str, args: Optional[tuple] = None, allow_raise: bool = False) -> None: ...
def dispatch_with_results(event_id: str, args: Optional[tuple] = None) -> EventResultDict: ...
def flatten_key_value(root_key: str, value: Any) -> dict[str, Any]: ...
def is_sequence(obj: Any) -> bool: ...
def seed() -> None: ...
def rand64bits() -> int: ...
def generate_128bit_trace_id() -> int: ...

class config:
    """Native config module for tracer configuration managed in Rust."""

    @staticmethod
    def get_128_bit_trace_id_enabled() -> bool:
        """Return whether 128-bit trace ID generation is enabled."""
        ...
    @staticmethod
    def set_128_bit_trace_id_enabled(val: bool) -> None:
        """Set whether 128-bit trace ID generation is enabled."""
        ...
    @staticmethod
    def get_raise() -> bool:
        """Return whether errors in event listeners should be re-raised (DD_TESTING_RAISE)."""
        ...
    @staticmethod
    def set_raise(val: bool) -> None:
        """Set whether errors in event listeners should be re-raised (DD_TESTING_RAISE)."""
        ...

# -----------------------------------------------------------------------------
# Native HTTP client (libdd-http-client wrapper)
# -----------------------------------------------------------------------------

class HttpMethod(Enum):
    """Standard HTTP methods understood by ``HttpRequest``."""

    Get = ...
    Post = ...
    Put = ...
    Delete = ...
    Head = ...
    Patch = ...
    Options = ...

class MultipartPart:
    """A single part in a multipart/form-data request body.

    The ``data`` parameter accepts any object that exposes the Python buffer
    protocol (``bytes``, ``bytearray``, ``memoryview``). The bytes are wrapped
    zero-copy via ``bytes::Bytes::from_owner``.
    """

    def __init__(
        self,
        name: str,
        data: bytes,
        *,
        filename: Optional[str] = None,
        content_type: Optional[str] = None,
    ) -> None: ...

class HttpRequest:
    """An outgoing HTTP request, built up via fluent ``with_*`` methods.

    ``url`` must be a full absolute URL (``http://`` or ``https://``). UDS is
    configured on the client builder via ``HttpClientBuilder.set_unix_socket``,
    not via the URL scheme.

    The request is consumed by ``HttpClient.send`` — calling any method on the
    same instance after ``send`` raises :class:`ValueError`. To retry, build a
    fresh request.

    Chain example:

        >>> part = MultipartPart("payload", b"{}", content_type="application/json")
        >>> req = (
        ...     HttpRequest(HttpMethod.Post, "http://localhost:8126/v0.4/traces")
        ...     .with_header("Content-Type", "application/json")
        ...     .with_multipart_part(part)
        ... )
    """

    def __init__(self, method: HttpMethod, url: str) -> None: ...
    @property
    def method(self) -> HttpMethod: ...
    @property
    def url(self) -> str: ...
    @property
    def headers(self) -> list[tuple[str, str]]: ...
    def with_header(self, name: str, value: str) -> "HttpRequest": ...
    def with_headers(self, headers: Iterable[tuple[str, str]]) -> "HttpRequest": ...
    def with_body(self, body: bytes) -> "HttpRequest": ...
    def with_timeout_ms(self, ms: int) -> "HttpRequest": ...
    def with_multipart_part(self, part: MultipartPart) -> "HttpRequest":
        """Append a multipart form-data part.

        Setting both ``with_body(...)`` and ``with_multipart_part(...)`` on the
        same request raises :class:`InvalidConfigError` from
        :meth:`HttpClient.send` (not from this method).
        """
        ...

class HttpResponse:
    """An HTTP response. Immutable; safe to share across threads."""

    @property
    def status_code(self) -> int: ...
    @property
    def headers(self) -> list[tuple[str, str]]:
        """Response headers as a list preserving insertion order and duplicates.

        Multiple ``Set-Cookie`` headers, for example, are kept as distinct
        list entries.
        """
        ...
    def body(self) -> bytes:
        """Return the response body as ``bytes``.

        The native client does **not** auto-decompress responses; callers
        handle their own decompression. Repeated calls return the same
        ``bytes`` object (memoized).
        """
        ...
    def header(self, name: str) -> Optional[str]:
        """Case-insensitive header lookup; returns the first matching value."""
        ...

class HttpClient:
    """A pooled HTTP client. Constructed via :meth:`HttpClientBuilder.build`.

    Instances are safe to share across Python threads; the underlying reqwest
    client pools connections internally (max 32 idle per host, 90s idle
    timeout — reqwest defaults). The GIL is released for the duration of every
    HTTP I/O call.

    Fork safety: the shared runtime returned by
    ``ddtrace.internal.native_runtime.get_native_runtime`` already wires
    ``before_fork``/``after_fork_*`` hooks into ``forksafe``. Callers do not
    invoke them.

    Notes on libdd defaults that are NOT user-configurable in v1:

    - Redirects are followed automatically (up to 10).
    - ``HTTP_PROXY`` / ``HTTPS_PROXY`` / ``NO_PROXY`` environment variables
      are honored by the underlying reqwest client.
    - Response bodies are NOT auto-decompressed; the ``Content-Encoding``
      header is preserved verbatim.
    """

    def send(self, request: HttpRequest) -> HttpResponse:
        """Send a fully-built request, releasing the GIL for the call.

        The ``request`` is consumed: subsequent operations on the same
        ``HttpRequest`` raise :class:`ValueError`.

            >>> req = HttpRequest(HttpMethod.Post, "http://localhost:8126/v0.4/traces").with_body(b"...")
            >>> resp = client.send(req)
        """
        ...

    def get(
        self,
        url: str,
        *,
        headers: Optional[Iterable[tuple[str, str]]] = None,
        timeout_ms: Optional[int] = None,
    ) -> HttpResponse:
        """Convenience GET. Headers must be an iterable of ``(name, value)`` tuples.

        >>> resp = client.get("http://localhost:8126/info", headers=[("Accept", "application/json")])
        """
        ...

    def post(
        self,
        url: str,
        *,
        headers: Optional[Iterable[tuple[str, str]]] = None,
        body: Optional[bytes] = None,
        timeout_ms: Optional[int] = None,
    ) -> HttpResponse:
        """Convenience POST. Headers must be an iterable of ``(name, value)`` tuples.

        >>> resp = client.post(
        ...     "http://localhost:8126/v0.4/traces",
        ...     headers=[("Content-Type", "application/msgpack")],
        ...     body=payload,
        ... )
        """
        ...

    def put(
        self,
        url: str,
        *,
        headers: Optional[Iterable[tuple[str, str]]] = None,
        body: Optional[bytes] = None,
        timeout_ms: Optional[int] = None,
    ) -> HttpResponse: ...
    def delete(
        self,
        url: str,
        *,
        headers: Optional[Iterable[tuple[str, str]]] = None,
        timeout_ms: Optional[int] = None,
    ) -> HttpResponse: ...
    def head(
        self,
        url: str,
        *,
        headers: Optional[Iterable[tuple[str, str]]] = None,
        timeout_ms: Optional[int] = None,
    ) -> HttpResponse: ...
    def patch(
        self,
        url: str,
        *,
        headers: Optional[Iterable[tuple[str, str]]] = None,
        body: Optional[bytes] = None,
        timeout_ms: Optional[int] = None,
    ) -> HttpResponse: ...
    def shutdown(self) -> None:
        """Close the underlying client. Subsequent ``send`` calls raise :class:`ValueError`.

        Optional — ``__del__`` runs the same close path.
        """
        ...

class HttpClientBuilder:
    """Builder for :class:`HttpClient`. Chain setters then call :meth:`build`.

        >>> from ddtrace.internal.native import HttpClientBuilder
        >>> from ddtrace.internal.native_runtime import get_native_runtime
        >>> client = (
        ...     HttpClientBuilder()
        ...     .set_timeout_ms(2000)
        ...     .build(get_native_runtime())
        ... )

    See also :func:`ddtrace.internal.utils.http.build_native_http_client` which
    handles URL-scheme dispatch (``http``/``https``/``unix``).
    """

    def __init__(self) -> None: ...
    def set_timeout_ms(self, ms: int) -> "HttpClientBuilder": ...
    def set_treat_http_errors_as_errors(self, value: bool) -> "HttpClientBuilder":
        """Default ``True``. When ``True``, HTTP 4xx/5xx responses raise
        :class:`RequestFailedError`. When ``False``, they are returned as
        regular :class:`HttpResponse` objects.
        """
        ...
    def set_retry(
        self,
        max_retries: int,
        initial_delay_ms: int = 100,
        jitter: bool = True,
    ) -> "HttpClientBuilder":
        """Enable automatic retries with exponential backoff.

        Gotcha: libdd retries **all** non-``InvalidConfig`` errors by default,
        including 4xx/5xx. For "no retry on 4xx" combine
        ``set_treat_http_errors_as_errors(False)`` with ``set_retry(N)`` — 4xx
        will be returned as a normal response and only connection/IO errors
        will be retried.
        """
        ...
    def set_unix_socket(self, path: str) -> "HttpClientBuilder":
        """Route all requests over the given Unix Domain Socket.

        The host portion of each ``HttpRequest`` URL is ignored when this is
        set. Raises :class:`ValueError` on Windows.
        """
        ...
    def set_allow_connection_pooling(self, allow: bool) -> "HttpClientBuilder": ...
    def build(self, shared_runtime: SharedRuntime) -> HttpClient:
        """Consume the builder and return a configured :class:`HttpClient`.

        The runtime is typically
        ``ddtrace.internal.native_runtime.get_native_runtime()``. Raises
        :class:`ValueError` if the builder has already been consumed.
        """
        ...

class HttpClientError(Exception):
    """Base class for all native HTTP client errors.

    Catch this to handle any failure from :meth:`HttpClient.send`. The
    granular subclasses are :class:`ConnectionFailedError`,
    :class:`TimedOutError`, :class:`RequestFailedError`,
    :class:`InvalidConfigError`, and :class:`HttpIoError`.

    Subclassable from Python:

        >>> class MyError(HttpClientError): ...

    Example exception handling:

        >>> try:
        ...     resp = client.send(req)
        ... except TimedOutError:
        ...     ...  # retry or surface
        ... except ConnectionFailedError as e:
        ...     ...  # cannot reach server
        ... except RequestFailedError as e:
        ...     status = e.status  # int
        ...     body = e.body  # str (lossy-UTF-8 decoded by libdd)
        ... except HttpClientError:
        ...     ...  # fall-through
    """

class ConnectionFailedError(HttpClientError):
    """TCP/socket connection to the server could not be established."""

class TimedOutError(HttpClientError):
    """The request exceeded its configured timeout."""

class RequestFailedError(HttpClientError):
    """The server returned an HTTP 4xx/5xx status code.

    Only raised when ``treat_http_errors_as_errors=True`` (the default).
    """

    status: int
    body: str  # NOTE: binary bodies are lossy-UTF-8 decoded; not byte-perfect.

class InvalidConfigError(HttpClientError):
    """The client/request configuration was invalid (e.g. zero timeout,
    both body and multipart parts set on the same request).
    """

class HttpIoError(HttpClientError):
    """An I/O error occurred during the request (truncated response,
    connection reset, etc.). Renamed from ``IoError`` at the Rust level to
    avoid a collision with :class:`IoError` from ``data_pipeline``.
    """
