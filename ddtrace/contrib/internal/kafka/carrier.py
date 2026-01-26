"""
Kafka carrier adapter for header extraction/injection.

This adapter knows how to extract headers from Kafka messages
and inject context into Kafka message headers.
"""

from typing import Any
from typing import Dict
from typing import Optional

from ddtrace.internal.core.integration.carrier import CarrierAdapter
from ddtrace.internal.utils import set_argument_value
from ddtrace.propagation.http import HTTPPropagator


class KafkaCarrierAdapter(CarrierAdapter):
    """
    Carrier adapter for Kafka.

    Handles:
    - Context extraction from message headers (for consumers)
    - Context injection into message headers (for producers)

    For extraction, expects payload to contain:
    - messages: list of Kafka Message objects

    For injection, expects payload to contain:
    - headers: dict of headers to inject into
    - args: tuple of positional args (optional, for updating)
    - kwargs: dict of keyword args (optional, for updating)
    """

    def extract(self, payload: Dict[str, Any]) -> Optional[Dict[str, str]]:
        """Extract headers from the first Kafka message."""
        messages = payload.get("messages", [])
        if not messages:
            return None

        first_message = messages[0]
        if first_message is None:
            return None

        # Get headers from message
        if hasattr(first_message, "headers") and callable(first_message.headers):
            msg_headers = first_message.headers()
            if msg_headers:
                return dict(msg_headers)

        return None

    def inject(self, payload: Dict[str, Any], context: Any) -> None:
        """Inject trace context into message headers and update args/kwargs."""
        # Get or create headers dict
        headers = payload.get("headers")
        if headers is None:
            headers = {}
            payload["headers"] = headers

        # Inject context into headers
        HTTPPropagator.inject(context, headers)

        # Update args/kwargs with the new headers if they exist
        args = payload.get("args")
        kwargs = payload.get("kwargs")
        if args is not None or kwargs is not None:
            args = args or ()
            kwargs = kwargs or {}
            args, kwargs = set_argument_value(args, kwargs, 6, "headers", headers, override_unset=True)
            payload["args"] = args
            payload["kwargs"] = kwargs
