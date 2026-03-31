from typing import Any
from typing import Callable

from ddtrace.appsec._contrib.stripe.types import StripeCheckoutSession
from ddtrace.appsec._contrib.stripe.types import StripeEvent
from ddtrace.appsec._contrib.stripe.types import StripePaymentIntent
from ddtrace.appsec._patch_utils import try_unwrap
from ddtrace.appsec._patch_utils import try_wrap_function_wrapper
from ddtrace.internal import core


def _wrap_checkout_session_create(
    original_callable: Callable[..., StripeCheckoutSession],
    instance: object,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> StripeCheckoutSession:
    session = original_callable(*args, **kwargs)
    core.dispatch("appsec.stripe.checkout.session.create", (session,))
    return session


def _wrap_payment_intent_create(
    original_callable: Callable[..., StripePaymentIntent],
    instance: object,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> StripePaymentIntent:
    payment_intent = original_callable(*args, **kwargs)
    core.dispatch("appsec.stripe.payment_intent.create", (payment_intent,))
    return payment_intent


def _wrap_webhook_construct_event(
    original_callable: Callable[..., StripeEvent],
    instance: object,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> StripeEvent:
    event = original_callable(*args, **kwargs)
    core.dispatch("appsec.stripe.webhook.construct_event", (event,))
    return event


def _wrap_stripe_client_construct_event(
    original_callable: Callable[..., StripeEvent],
    instance: object,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> StripeEvent:
    event = original_callable(*args, **kwargs)
    core.dispatch("appsec.stripe.stripe_client.construct_event", (event,))
    return event


def patch() -> None:
    try_wrap_function_wrapper(
        "stripe.checkout",
        "Session.create",
        _wrap_checkout_session_create,
    )
    try_wrap_function_wrapper(
        "stripe.checkout._session_service",
        "SessionService.create",
        _wrap_checkout_session_create,
    )
    try_wrap_function_wrapper(
        "stripe",
        "PaymentIntent.create",
        _wrap_payment_intent_create,
    )
    try_wrap_function_wrapper(
        "stripe._payment_intent_service",
        "PaymentIntentService.create",
        _wrap_payment_intent_create,
    )
    try_wrap_function_wrapper(
        "stripe.webhook",
        "construct_event",
        _wrap_webhook_construct_event,
    )
    try_wrap_function_wrapper(
        "stripe._webhook",
        "Webhook.construct_event",
        _wrap_webhook_construct_event,
    )
    try_wrap_function_wrapper(
        "stripe._stripe_client",
        "StripeClient.construct_event",
        _wrap_stripe_client_construct_event,
    )


def unpatch() -> None:
    try_unwrap("stripe.checkout", "Session.create")
    try_unwrap("stripe.checkout._session_service", "SessionService.create")
    try_unwrap("stripe", "PaymentIntent.create")
    try_unwrap("stripe._payment_intent_service", "PaymentIntentService.create")
    try_unwrap("stripe.webhook", "construct_event")
    try_unwrap("stripe._webhook", "Webhook.construct_event")
    try_unwrap("stripe._stripe_client", "StripeClient.construct_event")
