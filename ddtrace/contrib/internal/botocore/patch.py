"""
Trace queries to aws api done via botocore client
"""

import collections
import json
from typing import Union  # noqa:F401

from botocore import __version__
import botocore.client
import botocore.exceptions
import botocore.parsers
import wrapt

import ddtrace
from ddtrace import config
from ddtrace._trace.pin import Pin

# AIDEV-NOTE: _http_propagation_suppressed is the shared seam telling the
# urllib3-layer subscriber to skip its own injection during AWS calls. See the
# ownership contract on its definition in ddtrace/_trace/subscribers/http_client.py.
from ddtrace._trace.subscribers.http_client import _http_propagation_suppressed
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib.internal.trace_utils import ext_service
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import with_traced_module
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.logger import get_logger
from ddtrace.internal.schema import schematize_cloud_api_operation
from ddtrace.internal.schema import schematize_cloud_faas_operation
from ddtrace.internal.schema import schematize_cloud_messaging_operation
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.settings import env
from ddtrace.internal.settings._config import Config
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.formats import deep_getattr
from ddtrace.llmobs._integrations import BedrockIntegration
from ddtrace.propagation.http import HTTPPropagator

from .services.bedrock import patched_bedrock_api_call
from .services.bedrock_agents import patched_bedrock_agents_api_call
from .services.kinesis import patched_kinesis_api_call
from .services.sqs import patched_sqs_api_call
from .services.sqs import update_messages as inject_trace_to_sqs_or_sns_message
from .services.stepfunctions import patched_stepfunction_api_call
from .services.stepfunctions import update_stepfunction_input
from .utils import update_agentcore_traceparent
from .utils import update_client_context
from .utils import update_eventbridge_detail


_PATCHED_SUBMODULES: set[str] = set()

# Original botocore client class
_Botocore_client = botocore.client.BaseClient

ARGS_NAME = ("action", "params", "path", "verb")
TRACED_ARGS = {"params", "path", "verb"}
PATCHING_FN_KEY = "PATCHING_FN"
SUPPORTED_OPS_KEY = "SUPPORTED_OPERATIONS"
ENDPOINTS_TO_PATCH_FUNCTIONS = {
    "bedrock-runtime": {
        PATCHING_FN_KEY: patched_bedrock_api_call,
        SUPPORTED_OPS_KEY: ["Converse", "ConverseStream", "InvokeModel", "InvokeModelWithResponseStream"],
    },
    "bedrock-agent-runtime": {
        PATCHING_FN_KEY: patched_bedrock_agents_api_call,
        SUPPORTED_OPS_KEY: ["InvokeAgent"],
    },
    "kinesis": {PATCHING_FN_KEY: patched_kinesis_api_call, SUPPORTED_OPS_KEY: None},
    "sqs": {PATCHING_FN_KEY: patched_sqs_api_call, SUPPORTED_OPS_KEY: None},
    "states": {PATCHING_FN_KEY: patched_stepfunction_api_call, SUPPORTED_OPS_KEY: None},
}

log = get_logger(__name__)


def _load_dynamodb_primary_key_names_for_tables() -> dict[str, set[str]]:
    try:
        encoded_table_primary_keys = env.get("DD_BOTOCORE_DYNAMODB_TABLE_PRIMARY_KEYS", "{}")
        raw_table_primary_keys = json.loads(encoded_table_primary_keys)

        table_primary_keys = {}
        for table, primary_keys in raw_table_primary_keys.items():
            if not isinstance(table, str):
                raise ValueError(f"expected string table name: {table}")

            if not isinstance(primary_keys, list):
                raise ValueError(f"expected list of primary keys: {primary_keys}")

            unique_primary_keys = set(primary_keys)
            if not len(unique_primary_keys) == len(primary_keys):
                raise ValueError(f"expected unique primary keys: {primary_keys}")

            table_primary_keys[table] = unique_primary_keys

        return table_primary_keys

    except Exception as e:
        log.warning("failed to load DD_BOTOCORE_DYNAMODB_TABLE_PRIMARY_KEYS: %s", e)
        return {}


_DD_BEFORE_SIGN_HANDLER_UID = "datadog-before-sign-inject"


def _inject_trace_headers_handler(request, **kwargs):
    """Inject propagation headers into the AWSRequest at botocore ``before-sign``.

    Injecting before signing makes the headers part of the SigV4 canonical
    request (injecting later, at the urllib3 layer, breaks strict-signature
    endpoints). Shared injection body; never registered directly — each
    integration registers an owner-gated wrapper (see
    ``_botocore_before_sign_handler`` / ``_aiobotocore_before_sign_handler``).
    Does NOT touch ``_http_propagation_suppressed``; see its ownership
    contract in ddtrace/_trace/subscribers/http_client.py.
    """
    if not config.botocore["distributed_tracing"]:
        return

    # Skip presigned-URL/POST signing modes (signature_version ends in
    # `-query` / contains `presign-post`): SigV4 query auth bakes the
    # signed-header list into the URL, so injecting here would force every
    # consumer of that URL to replay the headers or hit SignatureDoesNotMatch.
    # isinstance(str) guards Config(signature_version=botocore.UNSIGNED), where
    # before-sign still fires but signature_version is the UNSIGNED sentinel
    # (not a str), so .endswith would crash.
    signature_version = kwargs.get("signature_version")
    if isinstance(signature_version, str) and (
        signature_version.endswith("-query") or "presign-post" in signature_version
    ):
        return

    # AIDEV-NOTE: Uses the global tracer's current_span() because the before-sign
    # event hands us the AWSRequest, not the client, so there's no Pin to read here.
    span = ddtrace.tracer.current_span()
    if span is None:
        return

    propagation_headers: dict[str, str] = {}
    try:
        HTTPPropagator.inject(span.context, propagation_headers)
    except Exception:
        log.debug("dd-trace-py: HTTPPropagator.inject failed in before-sign handler", exc_info=True)
        return

    for header_name, header_value in propagation_headers.items():
        # Don't clobber a header the application set explicitly.
        if header_name in request.headers:
            continue
        request.headers[header_name] = header_value


def _botocore_before_sign_handler(request, **kwargs):
    """Before-sign handler for botocore clients; gates on botocore's own patch
    state so a retained registration goes inert once botocore is unpatched,
    even if aiobotocore stays patched (the asymmetric-unpatch case — gating on
    "either integration" would resurrect the SigV4 mismatch).
    """
    if not getattr(botocore.client, "_datadog_patch", False):
        return
    _inject_trace_headers_handler(request, **kwargs)


# AIDEV-NOTE: Also imported by ddtrace.contrib.internal.aiobotocore.patch;
# rename in lockstep. Each integration passes its own owner-gated handler.
def _ensure_before_sign_handler(client, handler) -> bool:
    """Register ``handler`` for ``before-sign`` on this client's emitter, once.
    Returns True if the handler is in place (already or newly registered).

    Client-level (not Session.__init__) on purpose: botocore copies a Session's
    handlers onto a client at creation time, so a handler added later never
    reaches an already-built client. Registering per-client at the first traced
    call covers clients created before ``patch()`` ran.

    The return value gates urllib3-layer suppression in the caller: if
    registration fails the handler can't inject pre-signing, so the caller must
    NOT suppress (else propagation is dropped at every layer). The sentinel is
    set only on success, so a failed attempt is retried on the next call.
    """
    if getattr(client, "_dd_before_sign_registered", False):
        return True
    try:
        client.meta.events.register(
            "before-sign",
            handler,
            unique_id=_DD_BEFORE_SIGN_HANDLER_UID,
        )
    except Exception:
        log.warning("dd-trace-py: failed to register botocore before-sign handler", exc_info=True)
        return False
    client._dd_before_sign_registered = True
    return True


# Botocore default settings
config._add(
    "botocore",
    {
        "_default_service": env.get("DD_BOTOCORE_SERVICE", default="aws"),
        "distributed_tracing": asbool(env.get("DD_BOTOCORE_DISTRIBUTED_TRACING", default=True)),
        "invoke_with_legacy_context": asbool(env.get("DD_BOTOCORE_INVOKE_WITH_LEGACY_CONTEXT", default=False)),
        "operations": collections.defaultdict(Config._HTTPServerConfig),
        "tag_no_params": asbool(env.get("DD_AWS_TAG_NO_PARAMS", default=False)),
        "instrument_internals": asbool(env.get("DD_BOTOCORE_INSTRUMENT_INTERNALS", default=False)),
        "propagation_enabled": asbool(env.get("DD_BOTOCORE_PROPAGATION_ENABLED", default=False)),
        "empty_poll_enabled": asbool(env.get("DD_BOTOCORE_EMPTY_POLL_ENABLED", default=True)),
        "dynamodb_primary_key_names_for_tables": _load_dynamodb_primary_key_names_for_tables(),
        "add_span_pointers": asbool(env.get("DD_BOTOCORE_ADD_SPAN_POINTERS", default=True)),
        "payload_tagging_request": env.get("DD_TRACE_CLOUD_REQUEST_PAYLOAD_TAGGING", default=None),
        "payload_tagging_response": env.get("DD_TRACE_CLOUD_RESPONSE_PAYLOAD_TAGGING", default=None),
        "payload_tagging_max_depth": int(
            env.get("DD_TRACE_CLOUD_PAYLOAD_TAGGING_MAX_DEPTH", 10)
        ),  # RFC defined 10 levels (1.2.3.4...10) as max tagging depth
        "payload_tagging_max_tags": int(
            env.get("DD_TRACE_CLOUD_PAYLOAD_TAGGING_MAX_TAGS", 758)
        ),  # RFC defined default limit - spans are limited past 1000
        "payload_tagging_services": set(
            service.strip()
            for service in env.get(
                "DD_TRACE_CLOUD_PAYLOAD_TAGGING_SERVICES", "s3,sns,sqs,kinesis,eventbridge,dynamodb"
            ).split(",")
        ),
    },
)


def get_version() -> str:
    return __version__


def _supported_versions() -> dict[str, str]:
    return {"botocore": "*"}


def patch():
    if getattr(botocore.client, "_datadog_patch", False):
        return
    botocore.client._datadog_patch = True

    botocore._datadog_integration = BedrockIntegration(integration_config=config.botocore)
    wrapt.wrap_function_wrapper("botocore.client", "BaseClient._make_api_call", patched_api_call(botocore))
    Pin().onto(botocore.client.BaseClient)
    wrapt.wrap_function_wrapper("botocore.parsers", "ResponseParser.parse", patched_lib_fn)
    Pin().onto(botocore.parsers.ResponseParser)
    _PATCHED_SUBMODULES.clear()


def unpatch():
    _PATCHED_SUBMODULES.clear()
    if getattr(botocore.client, "_datadog_patch", False):
        botocore.client._datadog_patch = False
        unwrap(botocore.parsers.ResponseParser, "parse")
        unwrap(botocore.client.BaseClient, "_make_api_call")
        # The before-sign handler is registered per-client on its own emitter
        # (see _ensure_before_sign_handler). We don't unregister from every
        # client that ever made a call; instead _inject_trace_headers_handler
        # self-gates on patch state, so any retained registration goes inert.


def patch_submodules(submodules: Union[list[str], bool]) -> None:
    if isinstance(submodules, bool) and submodules:
        _PATCHED_SUBMODULES.clear()
    elif isinstance(submodules, list):
        submodules = [sub_module.lower() for sub_module in submodules]
        _PATCHED_SUBMODULES.update(submodules)


def patched_lib_fn(original_func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled() or not config.botocore["instrument_internals"]:
        return original_func(*args, **kwargs)
    with (
        core.context_with_data(
            "botocore.instrumented_lib_function",
            span_name="{}.{}".format(original_func.__module__, original_func.__name__),
            tags={COMPONENT: config.botocore.integration_name, SPAN_KIND: SpanKind.CLIENT},
            pin=pin,
        ) as ctx,
        ctx.span,
    ):
        return original_func(*args, **kwargs)


@with_traced_module
def patched_api_call(botocore, pin, original_func, instance, args, kwargs):
    if not pin or not pin.enabled():
        return original_func(*args, **kwargs)

    endpoint_name = deep_getattr(instance, "_endpoint._endpoint_prefix")

    if _PATCHED_SUBMODULES and endpoint_name not in _PATCHED_SUBMODULES:
        return original_func(*args, **kwargs)

    trace_operation = schematize_cloud_api_operation(
        "{}.command".format(endpoint_name), cloud_provider="aws", cloud_service=endpoint_name
    )

    operation = get_argument_value(args, kwargs, 0, "operation_name", True)
    params = get_argument_value(args, kwargs, 1, "api_params", True)

    function_vars = {
        "endpoint_name": endpoint_name,
        "operation": operation,
        "params": params,
        "pin": pin,
        "trace_operation": trace_operation,
        "integration": botocore._datadog_integration,
    }

    patching_fn = patched_api_call_fallback
    patched_endpoint = ENDPOINTS_TO_PATCH_FUNCTIONS.get(endpoint_name)
    if patched_endpoint:
        supported_operations = patched_endpoint.get(SUPPORTED_OPS_KEY)
        if supported_operations is None or operation in supported_operations:
            patching_fn = patched_endpoint[PATCHING_FN_KEY]

    # Suppress the urllib3-layer injection for this AWS call so trace headers are
    # not injected post-signing. Three cases:
    #   - distributed_tracing off: opt-out — suppress so no headers go out at any
    #     layer (skip registering the handler, which would no-op anyway).
    #   - on + handler registered: it injected pre-signing, so urllib3 must not
    #     re-inject post-signing.
    #   - on + registration failed: the handler can't inject, so do NOT suppress;
    #     let urllib3 inject as a fallback rather than drop propagation entirely.
    # Suppression is set/reset only on this path, so the contextvar stays owned
    # here and early-return paths can't leak it.
    token = None
    if not config.botocore["distributed_tracing"]:
        token = _http_propagation_suppressed.set(True)
    elif _ensure_before_sign_handler(instance, _botocore_before_sign_handler):
        token = _http_propagation_suppressed.set(True)
    try:
        return patching_fn(
            original_func=original_func,
            instance=instance,
            args=args,
            kwargs=kwargs,
            function_vars=function_vars,
        )
    finally:
        if token is not None:
            _http_propagation_suppressed.reset(token)


def prep_context_injection(ctx, endpoint_name, operation, trace_operation, params):
    cloud_service = None
    injection_function = None
    schematization_function = schematize_cloud_messaging_operation

    if endpoint_name == "lambda" and operation == "Invoke":
        injection_function = update_client_context
        schematization_function = schematize_cloud_faas_operation
        cloud_service = "lambda"
    if endpoint_name == "events" and operation == "PutEvents":
        injection_function = update_eventbridge_detail
        cloud_service = "events"
    if endpoint_name == "sns" and "Publish" in operation:
        injection_function = inject_trace_to_sqs_or_sns_message
        cloud_service = "sns"
    if endpoint_name == "states" and (operation == "StartExecution" or operation == "StartSyncExecution"):
        injection_function = update_stepfunction_input
        cloud_service = "stepfunctions"
    if endpoint_name == "bedrock-agentcore" and operation == "InvokeAgentRuntime":
        injection_function = update_agentcore_traceparent
        cloud_service = "bedrock-agentcore"

    core.dispatch(
        "botocore.prep_context_injection.post",
        (ctx, cloud_service, schematization_function, injection_function, trace_operation),
    )


def patched_api_call_fallback(original_func, instance, args, kwargs, function_vars):
    # default patched api call that is used generally for several services / operations
    params = function_vars.get("params")
    trace_operation = function_vars.get("trace_operation")
    pin = function_vars.get("pin")
    endpoint_name = function_vars.get("endpoint_name")
    operation = function_vars.get("operation")

    with (
        core.context_with_data(
            "botocore.instrumented_api_call",
            instance=instance,
            args=args,
            params=params,
            endpoint_name=endpoint_name,
            operation=operation,
            service=schematize_service_name(
                "{}.{}".format(ext_service(pin, int_config=config.botocore), endpoint_name)
            ),
            pin=pin,
            span_name=function_vars.get("trace_operation"),
            span_type=SpanTypes.HTTP,
            span_key="instrumented_api_call",
        ) as ctx,
        ctx.span,
    ):
        core.dispatch("botocore.patched_api_call.started", (ctx,))
        if args and config.botocore["distributed_tracing"]:
            prep_context_injection(ctx, endpoint_name, operation, trace_operation, params)

        try:
            result = original_func(*args, **kwargs)
        except botocore.exceptions.ClientError as e:
            core.dispatch(
                "botocore.patched_api_call.exception",
                (
                    ctx,
                    e.response,
                    botocore.exceptions.ClientError,
                    config.botocore.operations[ctx.span.resource].is_error_code,
                ),
            )
            raise
        else:
            core.dispatch("botocore.patched_api_call.success", (ctx, result))
            return result
