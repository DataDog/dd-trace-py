from ddtrace.appsec._asm_request_context import call_waf_callback
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger


logger = get_logger(__name__)


def _on_checkout_session_create(session):
    try:
        mode = session.mode
        if mode != "payment":
            return

        discounts_coupon = None
        discounts_promotion_code = None
        if session.discounts:
            discount = session.discounts[0]
            coupon = discount.coupon
            if coupon:
                if isinstance(coupon, str):
                    discounts_coupon = coupon
                else:
                    discounts_coupon = coupon.id

            promotion_code = discount.promotion_code
            if promotion_code:
                if isinstance(promotion_code, str):
                    discounts_promotion_code = promotion_code
                else:
                    discounts_promotion_code = promotion_code.id

        total_details_amount_discount = None
        total_details_amount_shipping = None
        if session.total_details:
            total_details_amount_discount = session.total_details.amount_discount
            total_details_amount_shipping = session.total_details.amount_shipping

        payment_creation_data = {
            "integration": "stripe",
            "id": session.id,
            "amount_total": session.amount_total,
            "client_reference_id": session.client_reference_id,
            "currency": session.currency,
            "discounts.coupon": discounts_coupon,
            "discounts.promotion_code": discounts_promotion_code,
            "livemode": session.livemode,
            "total_details.amount_discount": total_details_amount_discount,
            "total_details.amount_shipping": total_details_amount_shipping,
        }

        call_waf_callback({"PAYMENT_CREATION": payment_creation_data})
    except AttributeError:
        logger.debug("can't extract payment creation data from Session object", exc_info=True)


def _on_payment_intent_create(payment_intent):
    try:
        payment_method = payment_intent.payment_method
        if not isinstance(payment_method, str):
            payment_method = payment_method.id

        payment_creation_data = {
            "integration": "stripe",
            "id": payment_intent.id,
            "amount": payment_intent.amount,
            "currency": payment_intent.currency,
            "livemode": payment_intent.livemode,
            "payment_method": payment_method,
        }

        call_waf_callback({"PAYMENT_CREATION": payment_creation_data})
    except AttributeError:
        logger.debug("can't extract payment creation data from PaymentIntent object", exc_info=True)


def _on_payment_intent_event(event):
    try:
        if event.type == "payment_intent.succeeded":
            waf_data_name = "PAYMENT_SUCCESS"

            payment_intent_webhook_data = {
                "payment_method": event.data.object.payment_method,
            }

        elif event.type == "payment_intent.payment_failed":
            waf_data_name = "PAYMENT_FAILURE"

            payment_intent_webhook_data = {
                "last_payment_error.code": event.data.object.last_payment_error.code,
                "last_payment_error.decline_code": event.data.object.last_payment_error.decline_code,
                "last_payment_error.payment_method.id": event.data.object.last_payment_error.payment_method.id,
                "last_payment_error.payment_method.type": event.data.object.last_payment_error.payment_method.type,
            }
        elif event.type == "payment_intent.canceled":
            waf_data_name = "PAYMENT_CANCELLATION"

            payment_intent_webhook_data = {
                "cancellation_reason": event.data.object.cancellation_reason,
            }
        else:
            return

        payment_intent_webhook_data |= {
            "integration": "stripe",
            "id": event.data.object.id,
            "amount": event.data.object.amount,
            "currency": event.data.object.currency,
            "livemode": event.data.object.livemode,
        }

        call_waf_callback({waf_data_name: payment_intent_webhook_data})
    except AttributeError:
        logger.debug("can't extract payment_intent event data from Event object", exc_info=True)


def listen():
    core.on("appsec.stripe.checkout.session.create", _on_checkout_session_create)
    core.on("appsec.stripe.payment_intent.create", _on_payment_intent_create)
    core.on("appsec.stripe.webhook.construct_event", _on_payment_intent_event)
    core.on("appsec.stripe.stripe_client.construct_event", _on_payment_intent_event)
    core.on("appsec.stripe.stripe_client.parse_event_notification", _on_payment_intent_event)
