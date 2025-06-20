from typing import Any, Callable, Dict, List, Optional, Tuple

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

# Result types for dispatch_with_results functionality
class ResultType:
    """Result type constants for EventResult."""
    RESULT_OK: int = 0
    RESULT_EXCEPTION: int = 1
    RESULT_UNDEFINED: int = -1

class EventResult:
    """Individual listener result from dispatch_with_results."""
    response_type: int
    value: Any
    exception: Optional[Exception]
    
    def __init__(self, response_type: int = -1, value: Any = None, exception: Optional[Exception] = None) -> None: ...
    def __bool__(self) -> bool: ...

class EventResultDict:
    """Dictionary-like container for event results with attribute access support."""
    
    def __init__(self) -> None: ...
    def __getitem__(self, key: str) -> EventResult: ...
    def __setitem__(self, key: str, value: EventResult) -> None: ...
    def __getattr__(self, name: str) -> EventResult: ...
    def __contains__(self, key: str) -> bool: ...
    def keys(self) -> List[str]: ...
    def values(self) -> List[EventResult]: ...
    def items(self) -> List[Tuple[str, EventResult]]: ...

# Event hub submodule - high-performance event dispatcher implemented in Rust
class event_hub:
    """High-performance event hub submodule containing all event dispatcher functions."""
    
    # Event hub classes available in the submodule
    class ResultType:
        """Result type constants for EventResult."""
        RESULT_OK: int = 0
        RESULT_EXCEPTION: int = 1
        RESULT_UNDEFINED: int = -1

    class EventResult:
        """Individual listener result from dispatch_with_results."""
        response_type: int
        value: Any
        exception: Optional[Exception]
        
        def __init__(self, response_type: int = -1, value: Any = None, exception: Optional[Exception] = None) -> None: ...
        def __bool__(self) -> bool: ...

    class EventResultDict:
        """Dictionary-like container for event results with attribute access support."""
        
        def __init__(self) -> None: ...
        def __getitem__(self, key: str) -> EventResult: ...
        def __setitem__(self, key: str, value: EventResult) -> None: ...
        def __getattr__(self, name: str) -> EventResult: ...
        def __contains__(self, key: str) -> bool: ...
        def keys(self) -> List[str]: ...
        def values(self) -> List[EventResult]: ...
        def items(self) -> List[Tuple[str, EventResult]]: ...
    

    # Module-level event hub functions (using global EventHub instance)
    @staticmethod
    def has_listeners(event_id: str) -> bool:
        """
        Check if there are listeners registered for the provided event_id.

        :param event_id: The event identifier to check
        :return: True if listeners exist, False otherwise
        """
        ...

    @staticmethod
    def on(event_id: str, callback: Callable[..., Any], name: Any = None) -> None:
        """
        Register a listener for the provided event_id.

        :param event_id: The event identifier to listen for
        :param callback: The callback function to invoke when the event is dispatched
        :param name: Optional name/ID for the callback (uses callback ID if None)
        """
        ...

    @staticmethod
    def on_all(callback: Callable[..., Any]) -> None:
        """
        Register a listener for all events emitted.

        :param callback: The callback function to invoke for all events
                        Called with (event_id, args) as arguments
        """
        ...

    @staticmethod
    def dispatch(event_id: str, args: Optional[Tuple[Any, ...]] = None) -> None:
        """
        Dispatch an event to all registered listeners.

        :param event_id: The event identifier to dispatch
        :param args: Optional tuple of arguments to pass to listeners
        """
        ...

    @staticmethod
    def dispatch_with_results(event_id: str, args: Optional[Tuple[Any, ...]] = None) -> EventResultDict:
        """
        Dispatch an event and collect individual results from each listener.

        :param event_id: The event identifier to dispatch
        :param args: Optional tuple of arguments to pass to listeners
        :return: EventResultDict containing results from each listener
        """
        ...

    @staticmethod
    def reset(event_id: Optional[str] = None, callback: Optional[Callable[..., Any]] = None) -> None:
        """
        Remove registered listeners.

        :param event_id: If provided, only clear listeners for this event
        :param callback: If provided, only remove this specific callback
        """
        ...
