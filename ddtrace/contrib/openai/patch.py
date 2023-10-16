import os
import sys
from typing import Optional
from typing import TYPE_CHECKING

from openai import version

from ddtrace import config
from ddtrace.contrib._trace_utils_llm import BaseLLMIntegration
from ddtrace.internal.agent import get_stats_url
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.logger import get_logger
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.formats import deep_getattr
from ddtrace.internal.wrapping import wrap

from . import _endpoint_hooks
from ...pin import Pin
from .utils import _format_openai_api_key


if TYPE_CHECKING:
    from ddtrace import Span


log = get_logger(__name__)


config._add(
    "openai",
    {
        "logs_enabled": asbool(os.getenv("DD_OPENAI_LOGS_ENABLED", False)),
        "metrics_enabled": asbool(os.getenv("DD_OPENAI_METRICS_ENABLED", True)),
        "span_prompt_completion_sample_rate": float(os.getenv("DD_OPENAI_SPAN_PROMPT_COMPLETION_SAMPLE_RATE", 1.0)),
        "log_prompt_completion_sample_rate": float(os.getenv("DD_OPENAI_LOG_PROMPT_COMPLETION_SAMPLE_RATE", 0.1)),
        "span_char_limit": int(os.getenv("DD_OPENAI_SPAN_CHAR_LIMIT", 128)),
        "_api_key": os.getenv("DD_API_KEY"),
    },
)


_RESOURCES = {
    "model": (
        "Model",
        {
            "list": _endpoint_hooks._ListHook,
            "retrieve": _endpoint_hooks._RetrieveHook,
        },
    ),
    "completion": (
        "Completion",
        {
            "create": _endpoint_hooks._CompletionHook,
        },
    ),
    "chat_completion": (
        "ChatCompletion",
        {
            "create": _endpoint_hooks._ChatCompletionHook,
        },
    ),
    "edit": (
        "Edit",
        {
            "create": _endpoint_hooks._EditHook,
        },
    ),
    "image": (
        "Image",
        {
            "create": _endpoint_hooks._ImageCreateHook,
            "create_edit": _endpoint_hooks._ImageEditHook,
            "create_variation": _endpoint_hooks._ImageVariationHook,
        },
    ),
    "audio": (
        "Audio",
        {
            "transcribe": _endpoint_hooks._AudioTranscriptionHook,
            "translate": _endpoint_hooks._AudioTranslationHook,
        },
    ),
    "embedding": (
        "Embedding",
        {
            "create": _endpoint_hooks._EmbeddingHook,
        },
    ),
    "moderation": (
        "Moderation",
        {
            "create": _endpoint_hooks._ModerationHook,
        },
    ),
    "file": (
        "File",
        {
            "create": _endpoint_hooks._FileCreateHook,
            "delete": _endpoint_hooks._DeleteHook,
            "download": _endpoint_hooks._FileDownloadHook,
        },
    ),
    "fine_tune": (
        "FineTune",
        {
            "create": _endpoint_hooks._FineTuneCreateHook,
            "cancel": _endpoint_hooks._FineTuneCancelHook,
        },
    ),
}


def get_version():
    # type: () -> str
    return version.VERSION


class _OpenAIIntegration(BaseLLMIntegration):
    _integration_name = "openai"

    def __init__(self, config, openai, stats_url, site, api_key):
        # FIXME: this currently does not consider if the tracer is configured to
        # use a different hostname. eg. tracer.configure(host="new-hostname")
        # Ideally the metrics client should live on the tracer or some other core
        # object that is strongly linked with configuration.
        super().__init__(config, stats_url, site, api_key)
        self._openai = openai

    @property
    def _user_api_key(self):
        # type: () -> Optional[str]
        """Get a representation of the user API key for tagging."""
        # Match the API key representation that OpenAI uses in their UI.
        if self._openai.api_key is None:
            return
        return "sk-...%s" % self._openai.api_key[-4:]

    def _set_base_span_tags(self, span):
        # type: (Span) -> None
        span.set_tag_str(COMPONENT, self._config.integration_name)
        if self._user_api_key is not None:
            span.set_tag_str("openai.user.api_key", self._user_api_key)

        # Do these dynamically as openai users can set these at any point
        # not necessarily before patch() time.
        # organization_id is only returned by a few endpoints, grab it when we can.
        for attr in ("api_base", "api_version", "api_type", "organization"):
            v = getattr(self._openai, attr, None)
            if v is not None:
                if attr == "organization":
                    span.set_tag_str("openai.organization.id", v or "")
                else:
                    span.set_tag_str("openai.%s" % attr, v)

    @classmethod
    def _logs_tags(cls, span):
        tags = "env:%s,version:%s,openai.request.endpoint:%s,openai.request.method:%s,openai.request.model:%s,openai.organization.name:%s," "openai.user.api_key:%s" % (  # noqa: E501
            (config.env or ""),
            (config.version or ""),
            (span.get_tag("openai.request.endpoint") or ""),
            (span.get_tag("openai.request.method") or ""),
            (span.get_tag("openai.request.model") or ""),
            (span.get_tag("openai.organization.name") or ""),
            (span.get_tag("openai.user.api_key") or ""),
        )
        return tags

    @classmethod
    def _metrics_tags(cls, span):
        tags = [
            "version:%s" % (config.version or ""),
            "env:%s" % (config.env or ""),
            "service:%s" % (span.service or ""),
            "openai.request.model:%s" % (span.get_tag("openai.request.model") or ""),
            "openai.request.endpoint:%s" % (span.get_tag("openai.request.endpoint") or ""),
            "openai.request.method:%s" % (span.get_tag("openai.request.method") or ""),
            "openai.organization.id:%s" % (span.get_tag("openai.organization.id") or ""),
            "openai.organization.name:%s" % (span.get_tag("openai.organization.name") or ""),
            "openai.user.api_key:%s" % (span.get_tag("openai.user.api_key") or ""),
            "error:%d" % span.error,
        ]
        err_type = span.get_tag("error.type")
        if err_type:
            tags.append("error_type:%s" % err_type)
        return tags

    def record_usage(self, span, usage):
        if not usage or not self._config.metrics_enabled:
            return
        tags = self._metrics_tags(span)
        tags.append("openai.estimated:false")
        for token_type in ("prompt", "completion", "total"):
            num_tokens = usage.get(token_type + "_tokens")
            if not num_tokens:
                continue
            span.set_metric("openai.response.usage.%s_tokens" % token_type, num_tokens)
            self._statsd.distribution("tokens.%s" % token_type, num_tokens, tags=tags)


def _wrap_classmethod(obj, wrapper):
    wrap(obj.__func__, wrapper)


def patch():
    # Avoid importing openai at the module level, eventually will be an import hook
    import openai

    if getattr(openai, "__datadog_patch", False):
        return

    ddsite = os.getenv("DD_SITE", "datadoghq.com")
    ddapikey = os.getenv("DD_API_KEY", config.openai._api_key)

    Pin().onto(openai)
    integration = _OpenAIIntegration(
        config=config.openai,
        openai=openai,
        stats_url=get_stats_url(),
        site=ddsite,
        api_key=ddapikey,
    )

    if config.openai.logs_enabled:
        if not ddapikey:
            raise ValueError("DD_API_KEY is required for sending logs from the OpenAI integration")
        integration.start_log_writer()

    import openai.api_requestor

    wrap(openai.api_requestor._make_session, _patched_make_session)
    wrap(openai.util.convert_to_openai_object, _patched_convert(openai, integration))

    for resource, resource_tuple in _RESOURCES.items():
        if hasattr(openai.api_resources, resource):
            resource_cls = resource_tuple[0]
            method_hook_dict = resource_tuple[1]
            for method_name, endpoint_hook in method_hook_dict.items():
                sync_method = deep_getattr(openai, "%s.%s" % (resource_cls, method_name))
                async_method = deep_getattr(openai, "%s.a%s" % (resource_cls, method_name))
                _wrap_classmethod(sync_method, _patched_endpoint(openai, integration, endpoint_hook))
                _wrap_classmethod(async_method, _patched_endpoint_async(openai, integration, endpoint_hook))

    # FineTune.list_events is the only traced endpoint that does not have an async version, so have to wrap it here.
    _wrap_classmethod(
        openai.api_resources.fine_tune.FineTune.list_events,
        _patched_endpoint(openai, integration, _endpoint_hooks._FineTuneListEventsHook),
    )

    openai.__datadog_patch = True


def unpatch():
    # FIXME: add unpatching. The current wrapping.unwrap method requires
    #        the wrapper function to be provided which we don't keep a reference to.
    pass


def _patched_make_session(func, args, kwargs):
    """Patch for `openai.api_requestor._make_session` which sets the service name on the
    requests session so that spans from the requests integration will use the service name openai.
    This is done so that the service break down will include OpenAI time spent querying the OpenAI backend.

    This should technically be a ``peer.service`` but this concept doesn't exist yet.
    """
    session = func(*args, **kwargs)
    service = schematize_service_name("openai")
    Pin.override(session, service=service)
    return session


def _traced_endpoint(endpoint_hook, integration, pin, args, kwargs):
    span = integration.trace(pin, endpoint_hook.OPERATION_ID)
    openai_api_key = _format_openai_api_key(kwargs.get("api_key"))
    err = None
    if openai_api_key:
        # API key can either be set on the import or per request
        span.set_tag_str("openai.user.api_key", openai_api_key)
    try:
        # Start the hook
        hook = endpoint_hook().handle_request(pin, integration, span, args, kwargs)
        hook.send(None)

        resp, err = yield

        # Record any error information
        if err is not None:
            span.set_exc_info(*sys.exc_info())
            integration.metric(span, "incr", "request.error", 1)

        # Pass the response and the error to the hook
        try:
            hook.send((resp, err))
        except StopIteration as e:
            if err is None:
                return e.value
    finally:
        # Streamed responses will be finished when the generator exits, so finish non-streamed spans here.
        # Streamed responses with error will need to be finished manually as well.
        if not kwargs.get("stream") or err is not None:
            span.finish()
            integration.metric(span, "dist", "request.duration", span.duration_ns)


def _patched_endpoint(openai, integration, patch_hook):
    def patched_endpoint(func, args, kwargs):
        pin = Pin._find(openai, args[0])
        if not pin or not pin.enabled():
            return func(*args, **kwargs)

        g = _traced_endpoint(patch_hook, integration, pin, args, kwargs)
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

    return patched_endpoint


def _patched_endpoint_async(openai, integration, patch_hook):
    # Same as _patched_endpoint but async
    async def patched_endpoint(func, args, kwargs):
        pin = Pin._find(openai, args[0])
        if not pin or not pin.enabled():
            return await func(*args, **kwargs)
        g = _traced_endpoint(patch_hook, integration, pin, args, kwargs)
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
                g.send((resp, err))
            except StopIteration as e:
                if err is None:
                    # This return takes priority over `return resp`
                    return e.value  # noqa: B012

    return patched_endpoint


def _patched_convert(openai, integration):
    def patched_convert(func, args, kwargs):
        """Patch convert captures header information in the openai response"""
        pin = Pin.get_from(openai)
        if not pin or not pin.enabled():
            return func(*args, **kwargs)

        span = pin.tracer.current_span()
        if not span:
            return func(*args, **kwargs)

        val = args[0]
        if not isinstance(val, openai.openai_response.OpenAIResponse):
            return func(*args, **kwargs)

        # This function is called for each chunk in the stream.
        # To prevent needlessly setting the same tags for each chunk, short-circuit here.
        if span.get_tag("openai.organization.name") is not None:
            return func(*args, **kwargs)

        val = val._headers
        if val.get("openai-organization"):
            org_name = val.get("openai-organization")
            span.set_tag_str("openai.organization.name", org_name)

        # Gauge total rate limit
        if val.get("x-ratelimit-limit-requests"):
            v = val.get("x-ratelimit-limit-requests")
            if v is not None:
                integration.metric(span, "gauge", "ratelimit.requests", int(v))
        if val.get("x-ratelimit-limit-tokens"):
            v = val.get("x-ratelimit-limit-tokens")
            if v is not None:
                integration.metric(span, "gauge", "ratelimit.tokens", int(v))

        # Gauge and set span info for remaining requests and tokens
        if val.get("x-ratelimit-remaining-requests"):
            v = val.get("x-ratelimit-remaining-requests")
            if v is not None:
                integration.metric(span, "gauge", "ratelimit.remaining.requests", int(v))
                span.set_metric("openai.organization.ratelimit.requests.remaining", int(v))
        if val.get("x-ratelimit-remaining-tokens"):
            v = val.get("x-ratelimit-remaining-tokens")
            if v is not None:
                integration.metric(span, "gauge", "ratelimit.remaining.tokens", int(v))
                span.set_metric("openai.organization.ratelimit.tokens.remaining", int(v))
        return func(*args, **kwargs)

    return patched_convert
