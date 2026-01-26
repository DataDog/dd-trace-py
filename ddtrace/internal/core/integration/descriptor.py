"""
Integration descriptor - static metadata for an integration.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING
from typing import Optional
from typing import Type


if TYPE_CHECKING:
    from .carrier import CarrierAdapter


@dataclass(frozen=True)
class IntegrationDescriptor:
    """
    Static metadata for an integration - no logic.

    This describes what kind of integration this is, allowing hooks
    to be automatically dispatched based on category and role.

    Attributes:
        name: The integration name (e.g., "kafka", "redis", "flask")
        category: The integration category (e.g., "messaging", "database", "web")
        role: The role within the category (e.g., "producer", "consumer", "server", "client")
        carrier_adapter: Optional adapter class for header extraction/injection
    """

    name: str
    category: str
    role: Optional[str] = None
    carrier_adapter: Optional[Type["CarrierAdapter"]] = None
