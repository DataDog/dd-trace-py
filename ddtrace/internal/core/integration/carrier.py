"""
Carrier adapters for header extraction/injection.

Different integrations have different header formats. Carrier adapters
abstract this difference so hooks can work with any integration.
"""

from abc import ABC
from abc import abstractmethod
from typing import Any
from typing import Dict
from typing import Optional


class CarrierAdapter(ABC):
    """
    Abstract base for header extraction/injection.

    Integrations with non-standard header formats (e.g., Kafka, SQS)
    should provide custom adapters.
    """

    @abstractmethod
    def extract(self, payload: Dict[str, Any]) -> Optional[Dict[str, str]]:
        """
        Extract headers from payload for context propagation.

        Args:
            payload: The event payload containing headers

        Returns:
            Headers dict or None if not present
        """
        pass

    @abstractmethod
    def inject(self, payload: Dict[str, Any], context: Any) -> None:
        """
        Inject trace context into payload headers.

        Args:
            payload: The event payload to inject headers into
            context: The trace context to inject
        """
        pass


class DefaultHeaderAdapter(CarrierAdapter):
    """
    Default adapter for standard header dict format.

    Works with integrations that use a simple {"headers": {...}} structure.
    """

    def extract(self, payload: Dict[str, Any]) -> Optional[Dict[str, str]]:
        """Extract headers from payload["headers"]."""
        return payload.get("headers")

    def inject(self, payload: Dict[str, Any], context: Any) -> None:
        """Inject trace context into payload["headers"]."""
        from ddtrace.propagation.http import HTTPPropagator

        headers = payload.setdefault("headers", {})
        HTTPPropagator.inject(context, headers)
