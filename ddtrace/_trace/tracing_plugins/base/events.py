"""
Event context types - the contract between patch and subscriber sides.

These dataclasses define the structured data passed from integration patch code
to tracing plugins via the core context system.
"""

from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Callable
from typing import Dict
from typing import Optional


@dataclass
class DatabaseContext:
    """
    Context passed by database integrations.

    Contains all information needed to create and tag a database span.
    """

    # Required - identifies the database system
    db_system: str  # "postgresql", "mysql", "sqlite", "mongodb", etc.

    # Query info
    query: Optional[str] = None

    # Connection info
    host: Optional[str] = None
    port: Optional[int] = None
    user: Optional[str] = None
    database: Optional[str] = None

    # DBM (Database Monitoring) propagation
    dbm_propagator: Optional[Any] = None

    # Extra tags specific to this integration
    tags: Dict[str, Any] = field(default_factory=dict)


@dataclass
class HTTPClientContext:
    """
    Context passed by HTTP client integrations.

    Contains request information for outbound HTTP calls.
    """

    # Required - request info
    method: str  # GET, POST, PUT, DELETE, etc.
    url: str

    # Target info for service naming and peer service
    target_host: Optional[str] = None
    target_port: Optional[int] = None

    # Request headers (for tagging, not modification)
    headers: Dict[str, str] = field(default_factory=dict)

    # Callback to inject distributed tracing headers into the request
    # The callback receives a dict of headers to add
    inject_headers: Optional[Callable[[Dict[str, str]], None]] = None

    # Extra tags
    tags: Dict[str, Any] = field(default_factory=dict)


@dataclass
class HTTPClientResponseContext:
    """
    Response context for HTTP clients.

    Set on the execution context after the response is received.
    """

    status_code: int
    headers: Dict[str, str] = field(default_factory=dict)


@dataclass
class WebContext:
    """
    Context passed by web framework integrations.

    Contains incoming request information for server-side handling.
    """

    # Required - request info
    method: str  # GET, POST, PUT, DELETE, etc.
    url: str
    path: str

    # Route info (may be set later after routing)
    route: Optional[str] = None

    # Headers for distributed tracing extraction
    headers: Dict[str, str] = field(default_factory=dict)

    # Query and path parameters
    query_params: Dict[str, str] = field(default_factory=dict)
    path_params: Dict[str, str] = field(default_factory=dict)

    # Client info
    remote_addr: Optional[str] = None

    # Extra tags
    tags: Dict[str, Any] = field(default_factory=dict)


@dataclass
class WebResponseContext:
    """
    Response context for web frameworks.

    Set on the execution context when the response is ready.
    """

    status_code: int
    headers: Dict[str, str] = field(default_factory=dict)


@dataclass
class MessagingContext:
    """
    Context passed by messaging integrations (Kafka, RabbitMQ, SQS, etc.).

    Used for both produce and consume operations.
    """

    # Required - identifies the messaging system
    messaging_system: str  # "kafka", "rabbitmq", "sqs", "sns", etc.

    # Required - destination (topic, queue, exchange)
    destination: str

    # Operation type
    operation: str  # "produce" or "consume"

    # Optional broker connection info (for peer service on produce)
    host: Optional[str] = None
    port: Optional[int] = None

    # Message metadata
    message_id: Optional[str] = None
    partition: Optional[int] = None
    offset: Optional[int] = None
    key: Optional[str] = None

    # For distributed tracing:
    # - On produce: inject_headers callback to add trace headers to message
    # - On consume: headers dict containing trace context from message
    headers: Dict[str, str] = field(default_factory=dict)
    inject_headers: Optional[Callable[[Dict[str, str]], None]] = None

    # Batch info (for batch produce/consume)
    batch_size: Optional[int] = None

    # Extra tags
    tags: Dict[str, Any] = field(default_factory=dict)
