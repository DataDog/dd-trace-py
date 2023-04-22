import os
import sys
import time

from ddtrace import config
from ddtrace.constants import SPAN_MEASURED_KEY
from ddtrace.internal.agent import get_stats_url
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.dogstatsd import get_dogstatsd_client
from ddtrace.internal.hostname import get_hostname
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.formats import asbool
from ddtrace.sampler import RateSampler

from .. import trace_utils
from .. import trace_utils_async
from ...pin import Pin
from ..trace_utils import set_flattened_tags
from ..trace_utils import wrap
from ._logging import V2LogWriter


config._add(
    "openai",
    {
        "logs_enabled": asbool(os.getenv("DD_OPENAI_LOGS_ENABLED", False)),
        "metrics_enabled": asbool(os.getenv("DD_OPENAI_METRICS_ENABLED", True)),
        "span_prompt_completion_sample_rate": float(os.getenv("DD_OPENAI_SPAN_PROMPT_COMPLETION_SAMPLE_RATE", 1.0)),
        "log_prompt_completion_sample_rate": float(os.getenv("DD_OPENAI_LOG_PROMPT_COMPLETION_SAMPLE_RATE", 0.1)),
        "span_char_limit": int(os.getenv("DD_OPENAI_SPAN_CHAR_LIMIT", 128)),
    },
)


class _OpenAIIntegration:
    def __init__(self, config, openai, stats_url, site, api_key):
        # FIXME: this currently does not consider if the tracer is configured to
        # use a different hostname. eg. tracer.configure(host="new-hostname")
        # Ideally the metrics client should live on the tracer or some other core
        # object that is strongly linked with configuration.
        self._statsd = get_dogstatsd_client(stats_url, namespace="openai")
        self._config = config
        self._log_writer = V2LogWriter(
            site=site,
            api_key=api_key,
            interval=float(os.getenv("_DD_OPENAI_LOG_WRITER_INTERVAL", "1.0")),
            timeout=float(os.getenv("_DD_OPENAI_LOG_WRITER_TIMEOUT", "2.0")),
        )
        self._span_pc_sampler = RateSampler(sample_rate=config.span_prompt_completion_sample_rate)
        self._log_pc_sampler = RateSampler(sample_rate=config.log_prompt_completion_sample_rate)
        self._statsd._enabled = config.metrics_enabled
        self._openai = openai

    def is_pc_sampled_span(self, span):
        if not span.sampled:
            return False
        return self._span_pc_sampler.sample(span)

    def is_pc_sampled_log(self, span):
        if not self._config.logs_enabled or not span.sampled:
            return False
        return self._log_pc_sampler.sample(span)

    def start_log_writer(self):
        self._log_writer.start()

    def _ust_tags(self):
        # Do this dynamically to ensure any changes to ddtrace.config.*
        # are respected here.
        return ["%s:%s" % (k, v) for k, v in [("env", config.env), ("version", config.version)] if v]

    def trace(self, pin, endpoint, model):
        """Start an OpenAI span.

        Set default OpenAI span attributes when possible.
        """
        resource = "%s/%s" % (endpoint, model) if model else endpoint
        span = pin.tracer.trace("openai.request", resource=resource, service=trace_utils.int_service(pin, self._config))
        span.set_tag(SPAN_MEASURED_KEY)
        span.set_tag_str(COMPONENT, self._config.integration_name)
        # Do these dynamically as openai users can set these at any point
        # not necessarily before patch() time.
        # organization_id is only returned by a few endpoints, grab it when we can.
        for attr in ("api_base", "api_version", "organization_id"):
            if hasattr(self._openai, attr):
                v = getattr(self._openai, attr)
                if v is not None:
                    if attr == "organization_id":
                        span.set_tag_str("organization.id", v)
                    else:
                        span.set_tag_str(attr, v)
        span.set_tag_str("endpoint", endpoint)
        if model:
            span.set_tag_str("model", model)
        return span

    def log(self, span, level, msg, attrs):
        if not self._config.logs_enabled:
            return
        tags = [
            "env:%s" % config.env,
            "version:%s" % config.version,
            "endpoint:%s" % span.get_tag("endpoint"),
            "model:%s" % span.get_tag("model"),
            "organization.name:%s" % span.get_tag("organization.name"),
        ]
        log = {
            "timestamp": time.time() * 1000,
            "message": msg,
            "hostname": get_hostname(),
            "ddsource": "openai",
            "service": span.service or "",
            "status": level,
            "ddtags": ",".join(t for t in tags),
        }
        if span is not None:
            log["dd.trace_id"] = str(span.trace_id)
            log["dd.span_id"] = str(span.span_id)
        log.update(attrs)
        self._log_writer.enqueue(log)

    def _metrics_tags(self, span):
        tags = [
            "version:%s" % (config.version or ""),
            "env:%s" % (config.env or ""),
            "service:%s" % (span.service or ""),
            "model:%s" % span.get_tag("model"),
            "endpoint:%s" % span.get_tag("endpoint"),
            "organization.id:%s" % span.get_tag("organization.id"),
            "organization.name:%s" % span.get_tag("organization.name"),
            "error:%d" % span.error,
        ]
        err_type = span.get_tag("error.type")
        if err_type:
            tags.append("error_type:%s" % err_type)
        return tags

    def metric(self, span, kind, name, val):
        """Set a metric using the OpenAI context from the given span."""
        if not self._config.metrics_enabled:
            return
        tags = self._metrics_tags(span)
        if kind == "dist":
            self._statsd.distribution(name, val, tags=tags)
        elif kind == "incr":
            self._statsd.increment(name, val, tags=tags)
        elif kind == "gauge":
            self._statsd.gauge(name, val, tags=tags)

    def record_usage(self, span, usage):
        if not usage:
            return
        tags = self._metrics_tags(span)
        for token_type in ["prompt", "completion", "total"]:
            num_tokens = usage.get(token_type + "_tokens")
            if not num_tokens:
                continue
            span.set_tag("response.usage.%s_tokens" % token_type, num_tokens)
            self._statsd.distribution("tokens.%s" % token_type, num_tokens, tags=tags)

    def trunc(self, text):
        """Truncate the given text.

        Use to avoid attaching too much data to spans.
        """
        if not text:
            return text
        text = text.replace("\n", "\\n")
        text = text.replace("\t", "\\t")
        text = " ".join(text.split())
        if len(text) > self._config.span_char_limit:
            text = text[: self._config.span_char_limit] + "..."
        return text


log = get_logger(__file__)


def patch():
    # Avoid importing openai at the module level, eventually will be an import hook
    import openai

    if getattr(openai, "__datadog_patch", False):
        return

    ddsite = os.getenv("DD_SITE", "datadoghq.com")
    ddapikey = os.getenv("DD_API_KEY", "")

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

    wrap(openai, "api_requestor._make_session", _patched_make_session(openai))
    wrap(openai, "util.convert_to_openai_object", _patched_convert(integration)(openai))

    if hasattr(openai.api_resources, "completion"):
        wrap(
            openai,
            "api_resources.completion.Completion.create",
            _patched_endpoint(integration, _CompletionHook)(openai),
        )
        wrap(
            openai,
            "api_resources.completion.Completion.acreate",
            _patched_endpoint_async(integration, _CompletionHook)(openai),
        )

    if hasattr(openai.api_resources, "chat_completion"):
        wrap(
            openai,
            "api_resources.chat_completion.ChatCompletion.create",
            _patched_endpoint(integration, _ChatCompletionHook)(openai),
        )
        wrap(
            openai,
            "api_resources.chat_completion.ChatCompletion.acreate",
            _patched_endpoint_async(integration, _ChatCompletionHook)(openai),
        )

    if hasattr(openai.api_resources, "embedding"):
        wrap(
            openai,
            "api_resources.embedding.Embedding.create",
            _patched_endpoint(integration, _EmbeddingHook)(openai),
        )
        wrap(
            openai,
            "api_resources.embedding.Embedding.acreate",
            _patched_endpoint_async(integration, _EmbeddingHook)(openai),
        )

    Pin().onto(openai)
    setattr(openai, "__datadog_patch", True)


def unpatch():
    # FIXME: add unpatching. unwrapping the create methods results in a
    # >               return super().create(*args, **kwargs)
    # E               AttributeError: 'method' object has no attribute '__get__'
    pass


@trace_utils.with_traced_module
def _patched_make_session(openai, pin, func, instance, args, kwargs):
    """Patch for `openai.api_requestor._make_session` which sets the service name on the
    requests session so that spans from the requests integration will use the service name openai.
    This is done so that the service break down will include OpenAI time spent querying the OpenAI backend.

    This should technically be a ``peer.service`` but this concept doesn't exist yet.
    """
    session = func()
    cfg = config.get_from(session)
    for k, v in cfg.items():
        if k not in pin._config:
            pin._config[k] = v
    pin.clone(service="openai").onto(session)
    return session


def _traced_endpoint(endpoint_hook, integration, openai, pin, instance, args, kwargs):
    span = integration.trace(pin, instance.OBJECT_NAME, kwargs.get("model"))
    try:
        # Start the hook
        hook = endpoint_hook().handle_request(integration, span, args, kwargs)
        hook.send(None)

        resp, error = yield

        # Record any error information
        if error is not None:
            span.set_exc_info(*sys.exc_info())
            integration.metric(span, "incr", "request.error", 1)

        # Pass the response and the error to the hook
        try:
            hook.send((resp, error))
        except StopIteration:
            pass
    finally:
        span.finish()
        integration.metric(span, "dist", "request.duration", span.duration_ns)


def _patched_endpoint(integration, patch_hook):
    @trace_utils.with_traced_module
    def patched_endpoint(openai, pin, func, instance, args, kwargs):
        g = _traced_endpoint(patch_hook, integration, openai, pin, instance, args, kwargs)
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
            except StopIteration:
                pass

    return patched_endpoint


def _patched_endpoint_async(integration, patch_hook):
    # Same as _patched_endpoint but async
    @trace_utils_async.with_traced_module
    async def patched_endpoint(openai, pin, func, instance, args, kwargs):
        g = _traced_endpoint(patch_hook, integration, openai, pin, instance, args, kwargs)
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
            except StopIteration:
                pass

    return patched_endpoint


class _EndpointHook:
    def handle_request(self, integration, span, args, kwargs):
        raise NotImplementedError


class _BaseCompletionHook(_EndpointHook):
    """Completion and ChatCompletion share a lot of logic, capture that here."""

    _request_tag_attrs = []

    def _record_request(self, span, kwargs):
        for kw_attr in self._request_tag_attrs:
            if kw_attr in kwargs:
                if isinstance(kwargs[kw_attr], dict):
                    set_flattened_tags(
                        span, [("request.{}.{}".format(kw_attr, k), v) for k, v in kwargs[kw_attr].items()]
                    )
                else:
                    span.set_tag("request.%s" % kw_attr, kwargs[kw_attr])


class _CompletionHook(_BaseCompletionHook):
    _request_tag_attrs = [
        "suffix",
        "max_tokens",
        "temperature",
        "top_p",
        "n",
        "stream",
        "logprobs",
        "echo",
        "stop",
        "presence_penalty",
        "frequency_penalty",
        "best_of",
        "logit_bias",
        "user",
    ]

    def handle_request(self, integration, span, args, kwargs):
        sample_pc_span = integration.is_pc_sampled_span(span)

        if sample_pc_span:
            prompt = kwargs.get("prompt", "")
            if isinstance(prompt, list):
                for idx, p in enumerate(prompt):
                    span.set_tag_str("request.prompt.%d" % idx, integration.trunc(p))
            elif prompt:
                span.set_tag_str("request.prompt", integration.trunc(prompt))

        self._record_request(span, kwargs)

        resp, error = yield

        if resp and not kwargs.get("stream"):
            if "choices" in resp:
                choices = resp["choices"]
                span.set_tag("response.choices.num", len(choices))
                for choice in choices:
                    idx = choice["index"]
                    if "finish_reason" in choice:
                        span.set_tag_str("response.choices.%d.finish_reason" % idx, str(choice["finish_reason"]))
                    if "logprobs" in choice:
                        span.set_tag_str("response.choices.%d.logprobs" % idx, "returned")
                    if sample_pc_span:
                        span.set_tag_str("response.choices.%d.text" % idx, integration.trunc(choice.get("text")))
            span.set_tag("response.object", resp["object"])
            integration.record_usage(span, resp.get("usage"))
            if integration.is_pc_sampled_log(span):
                prompt = kwargs.get("prompt", "")
                integration.log(
                    span,
                    "info" if error is None else "error",
                    "sampled completion",
                    attrs={
                        "prompt": prompt,
                        "choices": resp["choices"] if resp and "choices" in resp else [],
                    },
                )


class _ChatCompletionHook(_BaseCompletionHook):
    _request_tag_attrs = [
        "temperature",
        "top_p",
        "n",
        "stream",
        "stop",
        "max_tokens",
        "presence_penalty",
        "frequency_penalty",
        "logit_bias",
        "user",
    ]

    def handle_request(self, integration, span, args, kwargs):
        sample_pc_span = integration.is_pc_sampled_span(span)
        messages = kwargs.get("messages")
        if sample_pc_span and messages:

            def set_message_tag(m):
                content = integration.trunc(m.get("content", ""))
                role = integration.trunc(m.get("role", ""))
                span.set_tag_str("request.messages.%d.content" % idx, content)
                span.set_tag_str("request.messages.%d.role" % idx, role)

            if isinstance(messages, list):
                for idx, message in enumerate(messages):
                    set_message_tag(message)
            else:
                set_message_tag(messages)

        self._record_request(span, kwargs)

        resp, error = yield

        completions = ""
        if resp and not kwargs.get("stream"):
            if "choices" in resp:
                choices = resp["choices"]
                completions = choices
                span.set_tag("response.choices.num", len(choices))
                for choice in choices:
                    idx = choice["index"]
                    span.set_tag_str("response.choices.%d.finish_reason" % idx, choice.get("finish_reason"))
                    if sample_pc_span and choice.get("message"):
                        span.set_tag(
                            "response.choices.%d.message.content" % idx,
                            integration.trunc(choice.get("message").get("content")),
                        )
                        span.set_tag(
                            "response.choices.%d.message.role" % idx,
                            integration.trunc(choice.get("message").get("role")),
                        )
            span.set_tag("response.object", resp["object"])
            integration.record_usage(span, resp.get("usage"))

            if integration.is_pc_sampled_log(span):
                messages = kwargs.get("messages")
                integration.log(
                    span,
                    "info" if error is None else "error",
                    "sampled chat completion",
                    attrs={
                        "messages": messages,
                        "completion": completions,
                    },
                )


class _EmbeddingHook(_EndpointHook):
    def handle_request(self, integration, span, args, kwargs):
        for kw_attr in ["model", "input", "user"]:
            if kw_attr in kwargs:
                if kw_attr == "input" and isinstance(kwargs["input"], list):
                    for idx, inp in enumerate(kwargs["input"]):
                        span.set_tag_str("request.input.%d" % idx, integration.trunc(inp))
                span.set_tag("request.%s" % kw_attr, kwargs[kw_attr])

        resp, error = yield

        if resp:
            if "data" in resp:
                span.set_tag("response.data.num-embeddings", len(resp["data"]))
                span.set_tag("response.data.embedding-length", len(resp["data"][0]["embedding"]))
            for kw_attr in ["object", "usage"]:
                if kw_attr in kwargs:
                    span.set_tag("response.%s" % kw_attr, kwargs[kw_attr])
            integration.record_usage(span, resp.get("usage"))


def _patched_convert(integration):
    @trace_utils.with_traced_module
    def patched_convert(openai, pin, func, instance, args, kwargs):
        """Patch convert captures header information in the openai response"""
        for val in args:
            if isinstance(val, openai.openai_response.OpenAIResponse):
                span = pin.tracer.current_span()
                if not span:
                    return func(*args, **kwargs)
                val = val._headers
                if val.get("openai-organization"):
                    org_name = val.get("openai-organization")
                    span.set_tag("organization.name", org_name)

                # Gauge total rate limit
                if val.get("x-ratelimit-limit-requests"):
                    v = val.get("x-ratelimit-limit-requests")
                    integration.metric(span, "gauge", "ratelimit.requests", v)
                if val.get("x-ratelimit-limit-tokens"):
                    v = val.get("x-ratelimit-limit-tokens")
                    integration.metric(span, "gauge", "ratelimit.tokens", v)

                # Gauge and set span info for remaining requests and tokens
                if val.get("x-ratelimit-remaining-requests"):
                    v = val.get("x-ratelimit-remaining-requests")
                    integration.metric(span, "gauge", "ratelimit.remaining.requests", v)
                    span.set_tag("organization.ratelimit.requests.remaining", v)
                if val.get("x-ratelimit-remaining-tokens"):
                    v = val.get("x-ratelimit-remaining-tokens")
                    integration.metric(span, "gauge", "ratelimit.remaining.tokens", v)
                    span.set_tag("organization.ratelimit.tokens.remaining", v)
        return func(*args, **kwargs)

    return patched_convert
