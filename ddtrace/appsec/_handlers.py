import json
from typing import Any
from typing import Optional
from typing import Union

from ddtrace._trace.span import Span
from ddtrace.appsec._asm_request_context import _call_waf
from ddtrace.appsec._asm_request_context import _call_waf_first
from ddtrace.appsec._asm_request_context import _get_asm_context
from ddtrace.appsec._asm_request_context import call_waf_callback
from ddtrace.appsec._asm_request_context import get_blocked
from ddtrace.appsec._asm_request_context import set_body_response
from ddtrace.appsec._asm_request_context import should_analyze_body_response
from ddtrace.appsec._common_module_patches import _get_rasp_capability
from ddtrace.appsec._constants import APPSEC
from ddtrace.appsec._constants import EXPLOIT_PREVENTION
from ddtrace.appsec._constants import SPAN_DATA_NAMES
from ddtrace.appsec._http_utils import extract_cookies_from_headers
from ddtrace.appsec._http_utils import normalize_headers
from ddtrace.appsec._http_utils import parse_http_body
from ddtrace.contrib._events.http_client import HttpClientEvents
from ddtrace.contrib._events.http_client import HttpClientRequestEvent
from ddtrace.contrib.internal.httpx.utils import httpx_url_to_str
from ddtrace.internal import core
from ddtrace.internal import telemetry
from ddtrace.internal._exceptions import BlockingException
from ddtrace.internal.core import ExecutionContext
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings.asm import config as asm_config


logger = get_logger(__name__)


# set_http_meta


def _on_set_http_meta(
    span,
    request_ip,
    raw_uri,
    route,
    method,
    request_headers,
    request_cookies,
    parsed_query,
    request_path_params,
    request_body,
    status_code,
    response_headers,
    response_cookies,
):
    if asm_config._asm_enabled and span.span_type in asm_config._asm_http_span_types:
        # avoid circular import
        from ddtrace.appsec._asm_request_context import set_waf_address

        status_code = str(status_code) if status_code is not None else None

        addresses = [
            (SPAN_DATA_NAMES.REQUEST_HTTP_IP, request_ip),
            (SPAN_DATA_NAMES.REQUEST_URI_RAW, raw_uri),
            (SPAN_DATA_NAMES.REQUEST_ROUTE, route),
            (SPAN_DATA_NAMES.REQUEST_METHOD, method),
            (SPAN_DATA_NAMES.REQUEST_HEADERS_NO_COOKIES, request_headers),
            (SPAN_DATA_NAMES.REQUEST_COOKIES, request_cookies),
            (SPAN_DATA_NAMES.REQUEST_QUERY, parsed_query),
            (SPAN_DATA_NAMES.REQUEST_PATH_PARAMS, request_path_params),
            (SPAN_DATA_NAMES.REQUEST_BODY, request_body),
            (SPAN_DATA_NAMES.RESPONSE_STATUS, status_code),
            (SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES, response_headers),
        ]
        for k, v in addresses:
            if v is not None:
                set_waf_address(k, v)


# AWS Lambda
def _on_lambda_start_request(
    span: Span,
    request_headers: dict[str, str],
    request_ip: Optional[str],
    body: Optional[str],
    is_body_base64: bool,
    raw_uri: str,
    route: str,
    method: str,
    parsed_query: dict[str, Any],
    request_path_parameters: Optional[dict[str, Any]],
):
    if not (asm_config._asm_enabled and span.span_type in asm_config._asm_http_span_types):
        return

    headers = normalize_headers(request_headers)
    request_body = parse_http_body(headers, body, is_body_base64)
    request_cookies = extract_cookies_from_headers(headers)

    _on_set_http_meta(
        span,
        request_ip,
        raw_uri,
        route,
        method,
        headers,
        request_cookies,
        parsed_query,
        request_path_parameters,
        request_body,
        None,
        None,
        None,
    )

    _call_waf_first(("aws_lambda",))


def _on_lambda_start_response(
    span: Span,
    status_code: str,
    response_headers: dict[str, str],
):
    if not (asm_config._asm_enabled and span.span_type in asm_config._asm_http_span_types):
        return

    waf_headers = normalize_headers(response_headers)
    response_cookies = extract_cookies_from_headers(waf_headers)

    _on_set_http_meta(
        span,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        status_code,
        waf_headers,
        response_cookies,
    )

    _call_waf(("aws_lambda",))


def _on_lambda_parse_body(
    response_body: Optional[Union[str, dict[str, Any]]],
):
    if asm_config._api_security_feature_active:
        if response_body:
            set_body_response(response_body)


# gRPC


def _on_grpc_server_response(message):
    from ddtrace.appsec._asm_request_context import set_waf_address

    set_waf_address(SPAN_DATA_NAMES.GRPC_SERVER_RESPONSE_MESSAGE, message)


def _on_grpc_server_data(headers, request_message, method, metadata):
    from ddtrace.appsec._asm_request_context import set_headers
    from ddtrace.appsec._asm_request_context import set_waf_address

    set_headers(headers)
    if request_message is not None:
        set_waf_address(SPAN_DATA_NAMES.GRPC_SERVER_REQUEST_MESSAGE, request_message)

    set_waf_address(SPAN_DATA_NAMES.GRPC_SERVER_METHOD, method)

    if metadata:
        set_waf_address(SPAN_DATA_NAMES.GRPC_SERVER_REQUEST_METADATA, dict(metadata))


def _on_telemetry_periodic():
    try:
        telemetry.telemetry_writer.add_configurations(
            [
                (
                    APPSEC.ENV,
                    int(asm_config._asm_enabled),
                    asm_config.asm_enabled_origin,
                )
            ]
        )
    except Exception:
        logger.debug("Could not set appsec_enabled telemetry config status", exc_info=True)


# Stripe


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


# HTTPX
APPSEC_SSRF_ANALYZE_BODY_KEY = "appsec.ssrf_analyze_body"


def _on_httpx_request_started(ctx: ExecutionContext) -> None:
    if ctx.event.config.integration_name != "httpx":
        return

    if not _get_rasp_capability("ssrf"):
        return
    asm_context = _get_asm_context()
    if asm_context is None:
        return

    analyze_body = should_analyze_body_response(asm_context)
    ctx.set_item(APPSEC_SSRF_ANALYZE_BODY_KEY, analyze_body)


def _on_httpx_client_send_single_request_started(ctx: ExecutionContext) -> None:
    if not _get_rasp_capability("ssrf"):
        return

    asm_context = _get_asm_context()
    if asm_context is None:
        return

    request = ctx.get_item("request")
    if request is None:
        return

    raw_url = httpx_url_to_str(request.url)

    addresses = {
        EXPLOIT_PREVENTION.ADDRESS.SSRF: raw_url,
        "DOWN_REQ_METHOD": request.method,
        "DOWN_REQ_HEADERS": request.headers,
    }

    analyze_body = ctx.find_item(APPSEC_SSRF_ANALYZE_BODY_KEY)
    if analyze_body:
        try:
            addresses["DOWN_REQ_BODY"] = json.loads(request.content)
        except Exception:
            pass  # nosec
    call_waf_callback(
        addresses,
        rule_type=EXPLOIT_PREVENTION.TYPE.SSRF_REQ,
    )
    asm_context.downstream_requests += 1
    if blocking_config := get_blocked():
        raise BlockingException(blocking_config, EXPLOIT_PREVENTION.BLOCKING, EXPLOIT_PREVENTION.TYPE.SSRF, raw_url)


def _on_httpx_client_send_single_request_ended(ctx: ExecutionContext, exc_info) -> None:
    exc_type, _, _ = exc_info
    if exc_type is not None:
        return

    if not _get_rasp_capability("ssrf"):
        return

    response = ctx.get_item("response")
    if not response or not (300 <= response.status_code < 400):
        return

    addresses = {
        "DOWN_RES_STATUS": str(response.status_code),
        "DOWN_RES_HEADERS": response.headers,
    }

    call_waf_callback(
        addresses,
        rule_type=EXPLOIT_PREVENTION.TYPE.SSRF_RES,
    )


def _on_httpx_request_ended(ctx: ExecutionContext, exc_info) -> None:
    event: HttpClientRequestEvent = ctx.event

    if event.config.integration_name != "httpx":
        return

    exc_type, _, _ = exc_info
    if exc_type is not None:
        return

    if not _get_rasp_capability("ssrf"):
        return

    if event.response_status_code is None or (300 <= event.response_status_code < 400):
        return

    addresses = {
        "DOWN_RES_STATUS": str(event.response_status_code),
        "DOWN_RES_HEADERS": event.response_headers,
    }

    if ctx.get_item(APPSEC_SSRF_ANALYZE_BODY_KEY):
        response = ctx.get_item("response")
        if response is not None:
            try:
                addresses["DOWN_RES_BODY"] = response.json()
            except Exception:
                pass  # nosec

    call_waf_callback(
        addresses,
        rule_type=EXPLOIT_PREVENTION.TYPE.SSRF_RES,
    )


def listen():
    core.on("telemetry.periodic", _on_telemetry_periodic)

    core.on("set_http_meta_for_asm", _on_set_http_meta)

    core.on("aws_lambda.start_request", _on_lambda_start_request)
    core.on("aws_lambda.start_response", _on_lambda_start_response)
    core.on("aws_lambda.parse_body", _on_lambda_parse_body)

    core.on("appsec.stripe.checkout.session.create", _on_checkout_session_create)
    core.on("appsec.stripe.payment_intent.create", _on_payment_intent_create)
    core.on("appsec.stripe.webhook.construct_event", _on_payment_intent_event)
    core.on("appsec.stripe.stripe_client.construct_event", _on_payment_intent_event)
    core.on("appsec.stripe.stripe_client.parse_event_notification", _on_payment_intent_event)

    core.on("context.started.httpx.client._send_single_request", _on_httpx_client_send_single_request_started)
    core.on("context.ended.httpx.client._send_single_request", _on_httpx_client_send_single_request_ended)
    core.on(f"context.started.{HttpClientEvents.HTTP_REQUEST.value}", _on_httpx_request_started)
    core.on(f"context.ended.{HttpClientEvents.HTTP_REQUEST.value}", _on_httpx_request_ended)

    # disabling threats grpc listeners.
    # core.on("grpc.server.response.message", _on_grpc_server_response)
    # core.on("grpc.server.data", _on_grpc_server_data)
