import os
import sys

from openai import version

from ddtrace import config
from ddtrace.contrib.internal.openai import _endpoint_hooks
from ddtrace.contrib.internal.openai.utils import _format_openai_api_key
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import with_traced_module
from ddtrace.contrib.trace_utils import wrap
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.formats import deep_getattr
from ddtrace.internal.utils.version import parse_version
from ddtrace.llmobs._integrations import OpenAIIntegration
from ddtrace.trace import Pin


log = get_logger(__name__)


config._add(
    "openai",
    {
        "span_prompt_completion_sample_rate": float(os.getenv("DD_OPENAI_SPAN_PROMPT_COMPLETION_SAMPLE_RATE", 1.0)),
        "span_char_limit": int(os.getenv("DD_OPENAI_SPAN_CHAR_LIMIT", 128)),
    },
)


def get_version():
    # type: () -> str
    return version.VERSION


OPENAI_VERSION = parse_version(get_version())


_RESOURCES = {
    "models.Models": {
        "list": _endpoint_hooks._ModelListHook,
        "retrieve": _endpoint_hooks._ModelRetrieveHook,
        "delete": _endpoint_hooks._ModelDeleteHook,
    },
    "completions.Completions": {
        "create": _endpoint_hooks._CompletionHook,
    },
    "chat.Completions": {
        "create": _endpoint_hooks._ChatCompletionHook,
    },
    "images.Images": {
        "generate": _endpoint_hooks._ImageCreateHook,
        "edit": _endpoint_hooks._ImageEditHook,
        "create_variation": _endpoint_hooks._ImageVariationHook,
    },
    "audio.Transcriptions": {
        "create": _endpoint_hooks._AudioTranscriptionHook,
    },
    "audio.Translations": {
        "create": _endpoint_hooks._AudioTranslationHook,
    },
    "embeddings.Embeddings": {
        "create": _endpoint_hooks._EmbeddingHook,
    },
    "moderations.Moderations": {
        "create": _endpoint_hooks._ModerationHook,
    },
    "files.Files": {
        "create": _endpoint_hooks._FileCreateHook,
        "retrieve": _endpoint_hooks._FileRetrieveHook,
        "list": _endpoint_hooks._FileListHook,
        "delete": _endpoint_hooks._FileDeleteHook,
        "retrieve_content": _endpoint_hooks._FileDownloadHook,
    },
}

OPENAI_WITH_RAW_RESPONSE_ARG = "_dd.with_raw_response"


def patch():
    # Avoid importing openai at the module level, eventually will be an import hook
    import openai

    if getattr(openai, "__datadog_patch", False):
        return

    if OPENAI_VERSION < (1, 0, 0):
        log.warning("openai version %s is not supported, please upgrade to openai version 1.0 or later", OPENAI_VERSION)
        return

    Pin().onto(openai)
    integration = OpenAIIntegration(integration_config=config.openai, openai=openai)
    openai._datadog_integration = integration

    if OPENAI_VERSION >= (1, 8, 0):
        wrap(openai, "_base_client.SyncAPIClient._process_response", patched_convert(openai))
        wrap(openai, "_base_client.AsyncAPIClient._process_response", patched_convert(openai))
    else:
        wrap(openai, "_base_client.BaseClient._process_response", patched_convert(openai))
    wrap(openai, "OpenAI.__init__", patched_client_init(openai))
    wrap(openai, "AsyncOpenAI.__init__", patched_client_init(openai))
    wrap(openai, "AzureOpenAI.__init__", patched_client_init(openai))
    wrap(openai, "AsyncAzureOpenAI.__init__", patched_client_init(openai))
    wrap(
        openai, "resources.chat.CompletionsWithRawResponse.__init__", patched_completions_with_raw_response_init(openai)
    )
    wrap(openai, "resources.CompletionsWithRawResponse.__init__", patched_completions_with_raw_response_init(openai))
    wrap(
        openai,
        "resources.chat.AsyncCompletionsWithRawResponse.__init__",
        patched_completions_with_raw_response_init(openai),
    )
    wrap(
        openai, "resources.AsyncCompletionsWithRawResponse.__init__", patched_completions_with_raw_response_init(openai)
    )

    for resource, method_hook_dict in _RESOURCES.items():
        if deep_getattr(openai.resources, resource) is None:
            continue
        for method_name, endpoint_hook in method_hook_dict.items():
            sync_method = "resources.{}.{}".format(resource, method_name)
            async_method = "resources.{}.{}".format(".Async".join(resource.split(".")), method_name)
            wrap(openai, sync_method, _patched_endpoint(openai, endpoint_hook))
            wrap(openai, async_method, _patched_endpoint_async(openai, endpoint_hook))

    openai.__datadog_patch = True


def unpatch():
    import openai

    if not getattr(openai, "__datadog_patch", False):
        return

    if OPENAI_VERSION < (1, 0, 0):
        log.warning("openai version %s is not supported, please upgrade to openai version 1.0 or later", OPENAI_VERSION)
        return

    openai.__datadog_patch = False

    if OPENAI_VERSION >= (1, 8, 0):
        unwrap(openai._base_client.SyncAPIClient, "_process_response")
        unwrap(openai._base_client.AsyncAPIClient, "_process_response")
    else:
        unwrap(openai._base_client.BaseClient, "_process_response")
    unwrap(openai.OpenAI, "__init__")
    unwrap(openai.AsyncOpenAI, "__init__")
    unwrap(openai.AzureOpenAI, "__init__")
    unwrap(openai.AsyncAzureOpenAI, "__init__")
    unwrap(openai.resources.chat.CompletionsWithRawResponse, "__init__")
    unwrap(openai.resources.CompletionsWithRawResponse, "__init__")
    unwrap(openai.resources.chat.AsyncCompletionsWithRawResponse, "__init__")
    unwrap(openai.resources.AsyncCompletionsWithRawResponse, "__init__")

    for resource, method_hook_dict in _RESOURCES.items():
        if deep_getattr(openai.resources, resource) is None:
            continue
        for method_name, _ in method_hook_dict.items():
            sync_resource = deep_getattr(openai.resources, resource)
            async_resource = deep_getattr(openai.resources, ".Async".join(resource.split(".")))
            unwrap(sync_resource, method_name)
            unwrap(async_resource, method_name)

    delattr(openai, "_datadog_integration")


@with_traced_module
def patched_client_init(openai, pin, func, instance, args, kwargs):
    """
    Patch for `openai.OpenAI/AsyncOpenAI` client init methods to add the client object to the OpenAIIntegration object.
    """
    func(*args, **kwargs)
    integration = openai._datadog_integration
    integration._client = instance
    api_key = kwargs.get("api_key")
    if api_key is None:
        api_key = instance.api_key
    if api_key is not None:
        integration.user_api_key = api_key
    return


@with_traced_module
def patched_completions_with_raw_response_init(openai, pin, func, instance, args, kwargs):
    func(*args, **kwargs)
    if hasattr(instance, "create") and (
        isinstance(instance, openai.resources.completions.CompletionsWithRawResponse)
        or isinstance(instance, openai.resources.completions.AsyncCompletionsWithRawResponse)
    ):
        wrap(instance, "create", _patched_endpoint(openai, _endpoint_hooks._CompletionWithRawResponseHook))
    elif hasattr(instance, "create") and (
        isinstance(instance, openai.resources.chat.CompletionsWithRawResponse)
        or isinstance(instance, openai.resources.chat.AsyncCompletionsWithRawResponse)
    ):
        wrap(instance, "create", _patched_endpoint(openai, _endpoint_hooks._ChatCompletionWithRawResponseHook))
    return


def _traced_endpoint(endpoint_hook, integration, instance, pin, args, kwargs):
    client = getattr(instance, "_client", None)
    base_url = getattr(client, "_base_url", None) if client else None

    if not (kwargs.pop(OPENAI_WITH_RAW_RESPONSE_ARG, False) and kwargs.get("stream", False)):
        span = integration.trace(
            pin,
            endpoint_hook.OPERATION_ID,
            base_url=base_url,
        )
    # avoid creating a span if streaming with raw response
    else:
        span = None

    openai_api_key = _format_openai_api_key(kwargs.get("api_key"))
    err = None
    if openai_api_key and span:
        # API key can either be set on the import or per request
        span.set_tag_str("openai.user.api_key", openai_api_key)
    try:
        # Start the hook
        if span:
            hook = endpoint_hook().handle_request(pin, integration, instance, span, args, kwargs)
            hook.send(None)

        resp, err = yield

        # Record any error information
        if err is not None and span:
            span.set_exc_info(*sys.exc_info())

        # Pass the response and the error to the hook
        try:
            if span:
                hook.send((resp, err))
            else:
                return resp
        except StopIteration as e:
            if err is None:
                return e.value
    finally:
        # Streamed responses will be finished when the generator exits, so finish non-streamed spans here.
        # Streamed responses with error will need to be finished manually as well.
        if (not kwargs.get("stream") or err is not None) and span:
            span.finish()


def _patched_endpoint(openai, patch_hook):
    @with_traced_module
    def patched_endpoint(openai, pin, func, instance, args, kwargs):
        if (
            patch_hook is _endpoint_hooks._ChatCompletionWithRawResponseHook
            or patch_hook is _endpoint_hooks._CompletionWithRawResponseHook
        ):
            kwargs[OPENAI_WITH_RAW_RESPONSE_ARG] = True
            return func(*args, **kwargs)
        integration = openai._datadog_integration
        g = _traced_endpoint(patch_hook, integration, instance, pin, args, kwargs)
        g.send(None)
        resp, err = None, None
        try:
            resp = func(*args, **kwargs)
            return resp
        except Exception as e:
            err = e
            raise
        finally:
            try:
                g.send((resp, err))
            except StopIteration as e:
                if err is None:
                    # This return takes priority over `return resp`
                    return e.value  # noqa: B012

    return patched_endpoint(openai)


def _patched_endpoint_async(openai, patch_hook):
    # Same as _patched_endpoint but async
    @with_traced_module
    async def patched_endpoint(openai, pin, func, instance, args, kwargs):
        integration = openai._datadog_integration
        g = _traced_endpoint(patch_hook, integration, instance, pin, args, kwargs)
        g.send(None)
        resp, err = None, None
        try:
            resp = await func(*args, **kwargs)
            return resp
        except Exception as e:
            err = e
            raise
        finally:
            try:
                if resp is not None:
                    # openai responses cannot be None
                    # if resp is None, it is likely because the context
                    # of the request was cancelled, so we want that to propagate up properly
                    # see: https://github.com/DataDog/dd-trace-py/issues/10191
                    g.send((resp, err))
            except StopIteration as e:
                if err is None:
                    # This return takes priority over `return resp`
                    return e.value  # noqa: B012

    return patched_endpoint(openai)


@with_traced_module
def patched_convert(openai, pin, func, instance, args, kwargs):
    """Patch convert captures header information in the openai response"""
    span = pin.tracer.current_span()
    if not span:
        return func(*args, **kwargs)

    if OPENAI_VERSION < (1, 0, 0):
        resp = args[0]
        if not isinstance(resp, openai.openai_response.OpenAIResponse):
            return func(*args, **kwargs)
        headers = resp._headers
    else:
        resp = kwargs.get("response", {})
        headers = resp.headers
    # This function is called for each chunk in the stream.
    # To prevent needlessly setting the same tags for each chunk, short-circuit here.
    if span.get_tag("openai.organization.name") is not None:
        return func(*args, **kwargs)
    if headers.get("openai-organization"):
        org_name = headers.get("openai-organization")
        span.set_tag_str("openai.organization.name", org_name)

    # Gauge total rate limit
    if headers.get("x-ratelimit-limit-requests"):
        v = headers.get("x-ratelimit-limit-requests")
        if v is not None:
            span.set_metric("openai.organization.ratelimit.requests.limit", int(v))
    if headers.get("x-ratelimit-limit-tokens"):
        v = headers.get("x-ratelimit-limit-tokens")
        if v is not None:
            span.set_metric("openai.organization.ratelimit.tokens.limit", int(v))
    # Gauge and set span info for remaining requests and tokens
    if headers.get("x-ratelimit-remaining-requests"):
        v = headers.get("x-ratelimit-remaining-requests")
        if v is not None:
            span.set_metric("openai.organization.ratelimit.requests.remaining", int(v))
    if headers.get("x-ratelimit-remaining-tokens"):
        v = headers.get("x-ratelimit-remaining-tokens")
        if v is not None:
            span.set_metric("openai.organization.ratelimit.tokens.remaining", int(v))

    return func(*args, **kwargs)
