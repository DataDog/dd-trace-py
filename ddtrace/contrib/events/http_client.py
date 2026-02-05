from typing import Optional

from ddtrace.internal.core.events import TracedEvent
from ddtrace.internal.core.events import context_event
from ddtrace.internal.core.events import event_field


@context_event
class HttpClientRequestEvent(TracedEvent):
    """HTTP client request event — pure data, no span manipulation.

    Integrations create this with library-specific data.
    Tracing, AppSec, etc. subscribe via their own handlers.
    """

    event_name = "http.client.request"

    # HTTP-specific fields only — span metadata inherited from TracedEvent
    url: str = event_field(in_context=True)
    method: str = event_field(in_context=True)
    target_host: Optional[str] = event_field(default=None, in_context=True)
    query: Optional[object] = event_field(default=None, in_context=True)
    request_headers: object = event_field(in_context=True)
    request: object = event_field(in_context=True)
