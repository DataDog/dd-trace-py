from collections.abc import Callable

from stripe import PaymentIntent
from stripe import PaymentIntentService
from stripe import StripeClient
from stripe import Webhook
from stripe._event import Event
from stripe.checkout import Session
from stripe.checkout import SessionService

from ddtrace.appsec._contrib.stripe.types import StripeCheckoutSession
from ddtrace.appsec._contrib.stripe.types import StripeEvent
from ddtrace.appsec._contrib.stripe.types import StripePaymentIntent


_checkout_session: StripeCheckoutSession = Session()
_stripe_event: StripeEvent = Event()
_payment_intent: StripePaymentIntent = PaymentIntent()

_checkout_session_create: Callable[..., StripeCheckoutSession] = Session.create
_checkout_session_service_create: Callable[..., StripeCheckoutSession] = SessionService.create
_payment_intent_create: Callable[..., StripePaymentIntent] = PaymentIntent.create
_payment_intent_service_create: Callable[..., StripePaymentIntent] = PaymentIntentService.create
_webhook_construct_event: Callable[..., StripeEvent] = Webhook.construct_event
_stripe_client_construct_event: Callable[..., StripeEvent] = StripeClient.construct_event
