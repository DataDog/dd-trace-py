import json
import os
import time
from typing import Any
from unittest.mock import MagicMock

import pytest
import stripe
from stripe._version import VERSION
import vcr

from ddtrace._trace.tracer import Tracer
from ddtrace.ext import SpanTypes
from tests.utils import override_global_config


RULES_STRIPE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rules-stripe.json")

STRIPE_API_KEY = os.environ.get(
    "STRIPE_API_KEY",
    "fake_stripe_key",
)
stripe.api_key = STRIPE_API_KEY

major, minor, _ = VERSION.split(".")
stripe_client = stripe.StripeClient(STRIPE_API_KEY)
stripe_v1_client = stripe_client.v1 if (major, minor) >= ("12", "5") else stripe_client


def _scrub_response(response):
    payload = json.loads(response["body"]["string"])
    if "client_secret" in payload:
        payload["client_secret"] = "redacted"
    response["body"]["string"] = json.dumps(payload).encode()
    return response


@pytest.fixture(scope="session")
def stripe_vcr():
    yield vcr.VCR(
        cassette_library_dir=os.path.join(os.path.dirname(__file__), "cassettes"),
        match_on=["path"],
        filter_headers=["authorization"],
        before_record_response=_scrub_response,
        # Ignore requests to the agent
        ignore_localhost=True,
    )


@pytest.fixture(scope="session")
def stripe_coupon(stripe_vcr):
    with stripe_vcr.use_cassette("coupon_create.yaml"):
        return stripe.Coupon.create(duration="once", percent_off=25)


@pytest.fixture(scope="session")
def stripe_promotion_code(stripe_vcr, stripe_coupon):
    with stripe_vcr.use_cassette("promotion_code_create.yaml"):
        return stripe.PromotionCode.create(
            code="APPSEC_CHRISTMAS_2025", promotion={"type": "coupon", "coupon": stripe_coupon.id}
        )


@pytest.fixture(scope="session")
def stripe_customer(stripe_vcr):
    with stripe_vcr.use_cassette("customer_create.yaml"):
        return stripe.Customer.create(email="customer@example.com")


@pytest.fixture(scope="session")
def stripe_payment_method(stripe_vcr, stripe_customer):
    with stripe_vcr.use_cassette("payment_method_create.yaml"):
        payment_method = stripe.PaymentMethod.create(
            type="card",
            card={"token": "tok_visa"},
        )
        stripe.PaymentMethod.attach(payment_method.id, customer=stripe_customer.id)
        return payment_method


@pytest.fixture
def stripe_discount(request, stripe_coupon, stripe_promotion_code):
    if request.param == "promotion_code":
        return [{"promotion_code": stripe_promotion_code["id"]}]
    return [{"coupon": stripe_coupon["id"]}]


@pytest.mark.parametrize("expand", [["discounts.coupon", "discounts.promotion_code"], []])
@pytest.mark.parametrize("stripe_discount", ["coupon", "promotion_code"], indirect=True)
@pytest.mark.parametrize(
    "stripe_session_create",
    [
        lambda params: stripe.checkout.Session.create(**params),  # Global config API
        lambda params: stripe_v1_client.checkout.sessions.create(params=params),  # Client config API
    ],
    ids=["global", "client"],
)
def test_stripe_checkout_session_create(
    request,
    tracer: Tracer,
    stripe_vcr,
    expand,
    stripe_discount,
    stripe_session_create,
):
    config = {
        "_asm_enabled": True,
        "_asm_static_rule_file": RULES_STRIPE,
    }
    with override_global_config(config):
        tracer._recreate()
        with tracer.trace("request", service="test", span_type=SpanTypes.WEB) as span:
            expanded = "_expanded" if expand else ""
            discount_type = [key for key in stripe_discount[0]][0]
            with stripe_vcr.use_cassette(f"{request.node.originalname}{expanded}_{discount_type}.yaml"):
                session = stripe_session_create(
                    {
                        "expand": expand,
                        "success_url": "https://example.com/success",
                        "client_reference_id": "order_123",
                        "customer_email": "customer@example.com",
                        "line_items": [
                            {
                                "price_data": {
                                    "currency": "eur",
                                    "product_data": {
                                        "name": "Demo Product",
                                    },
                                    "unit_amount": 1000,
                                },
                                "quantity": 2,
                            }
                        ],
                        "mode": "payment",
                        "discounts": stripe_discount,
                        "shipping_options": [
                            {
                                "shipping_rate_data": {
                                    "display_name": "Standard",
                                    "fixed_amount": {"amount": 250, "currency": "eur"},
                                    "type": "fixed_amount",
                                }
                            }
                        ],
                    }
                )

    expected_tags = {
        "appsec.events.payments.integration": "stripe",
        "appsec.events.payments.create.id": session.id,
        "appsec.events.payments.create.currency": session.currency,
        "appsec.events.payments.create.client_reference_id": "order_123",
    }

    for tag, expected_value in expected_tags.items():
        assert span.get_tag(tag) == expected_value

    expected_metrics = {
        "appsec.events.payments.create.amount_total": session.amount_total,
        "appsec.events.payments.create.total_details.amount_discount": session.total_details.amount_discount,
        "appsec.events.payments.create.total_details.amount_shipping": session.total_details.amount_shipping,
        "appsec.events.payments.create.livemode": int(session.livemode),
    }

    for discount in stripe_discount:
        for key, value in discount.items():
            if key == "promotion_code":
                expected_tags["appsec.events.payments.create.promotion_code"] = value
            if key == "coupon":
                expected_tags["appsec.events.payments.create.coupon"] = value

    for metric, expected_value in expected_metrics.items():
        assert span.get_metric(metric) == expected_value


@pytest.mark.parametrize(
    "stripe_session_create",
    [
        lambda params: stripe.checkout.Session.create(**params),  # Global config API
        lambda params: stripe_v1_client.checkout.sessions.create(params=params),  # Client config API
    ],
    ids=["global", "client"],
)
@pytest.mark.parametrize(
    "payload",
    [
        {
            "success_url": "https://example.com/success",
            "currency": "eur",
            "mode": "setup",
        },
        {
            "success_url": "https://example.com/success",
            "line_items": [
                {
                    "price_data": {
                        "currency": "eur",
                        "product_data": {
                            "name": "Demo Product",
                        },
                        "unit_amount": 1000,
                        "recurring": {
                            "interval": "month",
                            "interval_count": 1,
                        },
                    },
                    "quantity": 2,
                }
            ],
            "mode": "subscription",
        },
    ],
    ids=["setup", "subscription"],
)
def test_stripe_checkout_session_ignore_setup(
    request, tracer: Tracer, stripe_vcr, stripe_session_create, monkeypatch, payload
):
    import ddtrace.appsec._handlers as handlers

    waf_callback = MagicMock(wraps=handlers.call_waf_callback)
    monkeypatch.setattr(handlers, "call_waf_callback", waf_callback)

    config = {
        "_asm_enabled": True,
        "_asm_static_rule_file": RULES_STRIPE,
    }
    with override_global_config(config):
        tracer._recreate()
        with tracer.trace("request", service="test", span_type=SpanTypes.WEB):
            with stripe_vcr.use_cassette(f"{request.node.originalname}_{payload['mode']}.yaml"):
                _session = stripe_session_create(payload)

    waf_callback.assert_not_called()


@pytest.mark.parametrize(
    "stripe_payment_intent_create",
    [
        lambda params: stripe.PaymentIntent.create(**params),  # Global config API
        lambda params: stripe_v1_client.payment_intents.create(params=params),  # Client config API
    ],
    ids=["global", "client"],
)
@pytest.mark.parametrize("expanded", [["payment_method"], []], ids=["expanded", "not_expanded"])
def test_stripe_payment_intent_create(
    request,
    tracer: Tracer,
    stripe_vcr,
    stripe_payment_intent_create,
    stripe_payment_method,
    stripe_customer,
    expanded,
):
    config = {
        "_asm_enabled": True,
        "_asm_static_rule_file": RULES_STRIPE,
    }
    with override_global_config(config):
        tracer._recreate()
        with tracer.trace("request", service="test", span_type=SpanTypes.WEB) as span:
            expand_param = expanded
            expanded_suffix = "_expanded" if expand_param else ""
            with stripe_vcr.use_cassette(f"{request.node.originalname}{expanded_suffix}.yaml"):
                params = {
                    "amount": 2000,
                    "currency": "usd",
                    "payment_method": stripe_payment_method.id,
                    "payment_method_types": ["card"],
                    "receipt_email": "customer@example.com",
                    "customer": stripe_customer.id,
                }

                if expand_param:
                    params["expand"] = expand_param

                session = stripe_payment_intent_create(params)

    expected_tags = {
        "appsec.events.payments.integration": "stripe",
        "appsec.events.payments.create.id": session.id,
        "appsec.events.payments.create.currency": session.currency,
        "appsec.events.payments.create.payment_method": stripe_payment_method.id,
    }

    for tag, expected_value in expected_tags.items():
        assert span.get_tag(tag) == expected_value

    expected_metrics = {
        "appsec.events.payments.create.livemode": int(session.livemode),
    }

    for metric, expected_value in expected_metrics.items():
        assert span.get_metric(metric) == expected_value


def _build_webhook_signature(payload, secret):
    payload_json = json.dumps(payload)
    timestamp = int(time.time())
    signed_payload = f"{timestamp}.{payload_json}"
    signature = stripe.WebhookSignature._compute_signature(signed_payload, secret)
    header = f"t={timestamp},v1={signature}"
    return payload_json, header


def include_webhook(path: str) -> dict[str, Any]:
    directory = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webhooks")
    with open(os.path.join(directory, path)) as file:
        return json.loads(file.read())


@pytest.mark.parametrize(
    "payload,expected_tags,expected_metrics",
    [
        (
            include_webhook("payment_succeeded.json"),
            lambda payment_intent: {
                "appsec.events.payments.integration": "stripe",
                "appsec.events.payments.success.id": payment_intent["id"],
                "appsec.events.payments.success.currency": payment_intent["currency"],
                "appsec.events.payments.success.payment_method": payment_intent["payment_method"],
            },
            lambda payment_intent: {
                "appsec.events.payments.success.amount": payment_intent["amount"],
                "appsec.events.payments.success.livemode": int(payment_intent["livemode"]),
            },
        ),
        (
            include_webhook("payment_failed.json"),
            lambda payment_intent: {
                "appsec.events.payments.integration": "stripe",
                "appsec.events.payments.failure.id": payment_intent["id"],
                "appsec.events.payments.failure.currency": payment_intent["currency"],
                "appsec.events.payments.failure.last_payment_error.code": payment_intent["last_payment_error"]["code"],
                "appsec.events.payments.failure.last_payment_error.decline_code": payment_intent["last_payment_error"][
                    "decline_code"
                ],
                "appsec.events.payments.failure.last_payment_error.payment_method.id": (
                    payment_intent["last_payment_error"]["payment_method"]["id"]
                ),
                "appsec.events.payments.failure.last_payment_error.payment_method.type": (
                    payment_intent["last_payment_error"]["payment_method"]["type"]
                ),
            },
            lambda payment_intent: {
                "appsec.events.payments.failure.amount": payment_intent["amount"],
                "appsec.events.payments.failure.livemode": int(payment_intent["livemode"]),
            },
        ),
        (
            include_webhook("payment_canceled.json"),
            lambda payment_intent: {
                "appsec.events.payments.integration": "stripe",
                "appsec.events.payments.cancellation.id": payment_intent["id"],
                "appsec.events.payments.cancellation.currency": payment_intent["currency"],
                "appsec.events.payments.cancellation.cancellation_reason": payment_intent["cancellation_reason"],
            },
            lambda payment_intent: {
                "appsec.events.payments.cancellation.amount": payment_intent["amount"],
                "appsec.events.payments.cancellation.livemode": int(payment_intent["livemode"]),
            },
        ),
    ],
)
@pytest.mark.parametrize(
    "stripe_construct_event",
    [
        lambda payload, signature_header, secret: stripe.Webhook.construct_event(payload, signature_header, secret),
        lambda payload, signature_header, secret: stripe_client.construct_event(payload, signature_header, secret),
    ],
)
def test_stripe_webhook_events(
    tracer: Tracer,
    payload,
    expected_tags,
    expected_metrics,
    stripe_construct_event,
):
    config = {
        "_asm_enabled": True,
        "_asm_static_rule_file": RULES_STRIPE,
    }

    payload_json, signature_header = _build_webhook_signature(payload, "whsec_test")

    with override_global_config(config):
        tracer._recreate()
        with tracer.trace("request", service="test", span_type=SpanTypes.WEB) as span:
            _event = stripe_construct_event(payload_json, signature_header, "whsec_test")

    payment_intent = payload["data"]["object"]

    for tag, value in expected_tags(payment_intent).items():
        assert span.get_tag(tag) == value

    for metric, value in expected_metrics(payment_intent).items():
        assert span.get_metric(metric) == value
