"""Product-owned declarations for lazily registering subscriber bundles."""

from collections.abc import Mapping
from dataclasses import dataclass
from importlib import import_module
import sys
from types import MappingProxyType
from types import ModuleType
from typing import Optional
from typing import Protocol
from typing import cast


class RegistrableSubscriber(Protocol):
    def register(self) -> None: ...

    def unregister(self) -> None: ...


@dataclass(frozen=True)
class SubscriberBundle:
    """Subscribers exported by one lazily imported product module.

    Subscriber classes must disable auto_register so importing the module does not
    activate listeners outside the product's lifecycle. Named listener registration
    supplies idempotency; bundles deliberately do not cache registration state.
    """

    module: str
    subscribers: tuple[str, ...]

    def _resolve(self, module: ModuleType) -> tuple[RegistrableSubscriber, ...]:
        # Resolve the whole declaration before changing listeners, so a misspelled
        # subscriber name cannot leave an otherwise valid bundle partly registered.
        return tuple(cast(RegistrableSubscriber, getattr(module, name)) for name in self.subscribers)

    def register(self) -> None:
        """Register the bundle, importing its module only when requested."""
        for subscriber in self._resolve(import_module(self.module)):
            subscriber.register()

    def unregister(self) -> None:
        """Remove the bundle's listeners after the owner has drained active contexts.

        Teardown never imports a previously unused product module. Products needing
        completion listeners for active requests must defer this operation.
        """
        module = sys.modules.get(self.module)
        if module is not None:
            for subscriber in reversed(self._resolve(module)):
                subscriber.unregister()


@dataclass(frozen=True)
class ProductRegistration:
    """Identify a product and declare its subscriber bundles in registration order.

    The product owns activation policy. A family selects one bundle; omitting it
    registers or unregisters every declared bundle. ProductManager discovery does
    not activate these declarations automatically.
    """

    name: str
    subscriber_bundles: Mapping[str, SubscriberBundle]

    def __post_init__(self) -> None:
        # Snapshot declarations so later mutation cannot change which listeners teardown owns.
        object.__setattr__(self, "subscriber_bundles", MappingProxyType(dict(self.subscriber_bundles)))

    def _bundles(self, family: Optional[str]) -> tuple[SubscriberBundle, ...]:
        if family is None:
            return tuple(self.subscriber_bundles.values())
        return (self.subscriber_bundles[family],)

    def register(self, family: Optional[str] = None) -> None:
        """Register all bundles or one family; an unknown family raises KeyError."""
        for bundle in self._bundles(family):
            bundle.register()

    def unregister(self, family: Optional[str] = None) -> None:
        """Unregister selected bundles in reverse order once their work has drained."""
        for bundle in reversed(self._bundles(family)):
            bundle.unregister()
