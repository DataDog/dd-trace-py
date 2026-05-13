from typing import Any
from typing import Literal
from typing import Optional
from typing import Protocol
from typing import Sequence
from typing import Union


class StripeCoupon(Protocol):
    @property
    def id(self) -> str: ...


class StripePromotionCode(Protocol):
    @property
    def id(self) -> str: ...


class StripeCheckoutSessionTotalDetails(Protocol):
    @property
    def amount_discount(self) -> int: ...

    @property
    def amount_shipping(self) -> Optional[int]: ...


class StripeCheckoutSessionDiscount(Protocol):
    @property
    def coupon(self) -> Optional[Union[str, StripeCoupon]]: ...  # noqa: F821

    @property
    def promotion_code(self) -> Optional[Union[str, StripePromotionCode]]: ...


class StripeCheckoutSession(Protocol):
    @property
    def id(self) -> str: ...

    @property
    def amount_total(self) -> Optional[int]: ...

    @property
    def mode(self) -> Literal["payment", "setup", "subscription"]: ...

    @property
    def discounts(self) -> Optional[Sequence[StripeCheckoutSessionDiscount]]: ...

    @property
    def total_details(self) -> Optional[StripeCheckoutSessionTotalDetails]: ...

    @property
    def client_reference_id(self) -> Optional[str]: ...

    @property
    def currency(self) -> Optional[str]: ...

    @property
    def livemode(self) -> bool: ...


class StripePaymentMethod(Protocol):
    @property
    def id(self) -> str: ...


class StripePaymentIntent(Protocol):
    @property
    def id(self) -> str: ...

    @property
    def amount(self) -> int: ...

    @property
    def currency(self) -> str: ...

    @property
    def livemode(self) -> bool: ...

    @property
    def payment_method(self) -> Optional[Union[str, StripePaymentMethod]]: ...


class StripeTypedPaymentMethod(StripePaymentMethod, Protocol):
    @property
    def type(self) -> str: ...


class StripeLastPaymentError(Protocol):
    @property
    def code(self) -> Optional[str]: ...

    @property
    def decline_code(self) -> Optional[str]: ...

    @property
    def payment_method(self) -> StripeTypedPaymentMethod: ...


class StripeEventPaymentIntent(Protocol):
    @property
    def id(self) -> str: ...

    @property
    def amount(self) -> int: ...

    @property
    def currency(self) -> str: ...

    @property
    def livemode(self) -> bool: ...


class StripeSucceededEventPaymentIntent(StripeEventPaymentIntent, Protocol):
    @property
    def payment_method(self) -> Optional[Union[str, StripePaymentMethod]]: ...


class StripeFailedEventPaymentIntent(StripeEventPaymentIntent, Protocol):
    @property
    def last_payment_error(self) -> StripeLastPaymentError: ...


class StripeCanceledEventPaymentIntent(StripeEventPaymentIntent, Protocol):
    @property
    def cancellation_reason(self) -> Optional[str]: ...


class StripeEventData(Protocol):
    @property
    def object(self) -> Any: ...


class StripeEvent(Protocol):
    @property
    def type(self) -> str: ...

    @property
    def data(self) -> StripeEventData: ...
