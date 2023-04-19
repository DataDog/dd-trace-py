import datetime
import os
import sys

from ddtrace import config
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
from ..trace_utils import wrap
from ._logging import V2LogWriter


config._add(
    "openai",
    {
        "logs_enabled": asbool(os.getenv("DD_OPENAI_LOGS_ENABLED", False)),
        "metrics_enabled": asbool(os.getenv("DD_OPENAI_METRICS_ENABLED", True)),
        "span_prompt_completion_sample_rate": float(os.getenv("DD_OPENAI_SPAN_PROMPT_COMPLETION_SAMPLE_RATE", 1.0)),
        "log_prompt_completion_sample_rate": float(os.getenv("DD_OPENAI_LOG_PROMPT_COMPLETION_SAMPLE_RATE", 0.1)),
        "truncation_threshold": int(os.getenv("DD_OPENAI_TRUNCATION_THRESHOLD", 512)),
        "_default_service": "openai",  # TODO: remove this and do DD_SERVICE-openai
    },
)


class _OpenAIIntegration:
    def __init__(self, config, openai, stats_url, site, api_key):
        # FIXME: this currently does not consider if the tracer is configured to
        # use a different hostname. eg. tracer.configure(host="new-hostname")
        # Ideally the metrics client should live on the tracer or some other core
        # object that is strongly linked with configuration.
        # FIXME: the dogstatsd client doesn't support multi-threaded usage
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

    def set_openai_span_tags(self, span):
        """Set default trace tags to apply to all OpenAI spans."""
        span.set_tag_str(COMPONENT, self._config.integration_name)
        # do these dynamically as openai users can set these at any point
        # not necessarily before patch() time.
        for attr in ("api_base", "api_version", "organization"):
            if hasattr(self._openai, attr):
                v = getattr(self._openai, attr)
                if v is not None:
                    if attr == "organization":
                        span.set_tag_str("organization.id", v)
                    else:
                        span.set_tag_str(attr, v)

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

        # TODO: should service be DD_SERVICE or DD_SERVICE-openai?
        # DD_SERVICE-openai doesn't make much sense
        # if we have peer span then we're ok?
        return ["%s:%s" % (k, v) for k, v in [("env", config.env), ("version", config.version)] if v]

    def trace(self, pin, endpoint, model):
        """Start an OpenAI span.

        Set default attributes when possible.
        """
        resource = "%s/%s" % (endpoint, model) if model else endpoint
        span = pin.tracer.trace("openai.request", resource=resource, service=trace_utils.int_service(pin, self._config))
        self.set_openai_span_tags(span)
        span.set_tag_str("endpoint", endpoint)
        if model:
            span.set_tag_str("model", model)
        return span

    def log(self, span, level, msg, attrs):
        if not self._config.logs_enabled:
            return
        # TODO: do we need the same metrics tags here?
        # we have the trace correlation, are logs tags expensive?
        tags = [
            "env:%s" % config.env,
            "version:%s" % config.version,
            "endpoint:%s" % span.get_tag("endpoint"),
        ]
        # FIXME: can we do a more efficient timestamp?
        timestamp = datetime.datetime.now().isoformat()
        log = {
            "message": "%s %s" % (timestamp, msg),
            "hostname": get_hostname(),
            "ddsource": "openai",
            "service": span.service,
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
            "version:%s" % config.version,
            "env:%s" % config.env,
            "service:%s" % span.service,
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
        tags = self._metrics_tags(span)
        if kind == "dist":
            self._statsd.distribution(name, val, tags=tags)
        elif kind == "incr":
            self._statsd.increment(name, val, tags=tags)
        elif kind == "gauge":
            self._statsd.gauge(name, val, tags=tags)

    def usage_metrics(self, span, usage):
        if not usage:
            return
        tags = self._metrics_tags(span)
        for token_type in ["prompt", "completion", "total"]:
            num_tokens = usage.get(token_type + "_tokens")
            if not num_tokens:
                continue
            self._statsd.distribution("tokens.%s" % token_type, num_tokens, tags=tags)

    def trunc(self, text):
        """Truncate the given text.

        Use to avoid attaching too much data to spans.
        """
        if not text:
            return text
        if len(text) > self._config.truncation_threshold:
            text = text[: self._config.truncation_threshold] + "..."
        return text


_integration = None

log = get_logger(__file__)


def patch():
    # Avoid importing openai at the module level, eventually will be an import hook
    import openai

    ddsite = os.getenv("DD_SITE", "datadoghq.com")
    ddapikey = os.getenv("DD_API_KEY", "")

    integration = _OpenAIIntegration(
        config=config.openai,
        openai=openai,
        stats_url=get_stats_url(),
        site=ddsite,
        api_key=ddapikey,
    )
    global _integration
    _integration = integration

    if config.openai.logs_enabled:
        if not ddapikey:
            raise ValueError("DD_API_KEY is required for sending logs from the OpenAI integration")
        integration.start_log_writer()

    if getattr(openai, "__datadog_patch", False):
        return

    wrap(openai, "api_requestor._make_session", patched_make_session(openai))
    wrap(openai, "util.convert_to_openai_object", patched_convert(integration)(openai))

    if hasattr(openai.api_resources, "completion"):
        wrap(
            openai,
            "api_resources.completion.Completion.create",
            _patched_endpoint(integration, _completion_create)(openai),
        )
        wrap(
            openai,
            "api_resources.completion.Completion.acreate",
            _patched_endpoint_async(integration, _completion_create)(openai),
        )

    if hasattr(openai.api_resources, "chat_completion"):
        wrap(
            openai,
            "api_resources.chat_completion.ChatCompletion.create",
            _patched_endpoint(integration, _chat_completion_create)(openai),
        )
        wrap(
            openai,
            "api_resources.chat_completion.ChatCompletion.acreate",
            _patched_endpoint_async(integration, _chat_completion_create)(openai),
        )

    if hasattr(openai.api_resources, "embedding"):
        wrap(
            openai,
            "api_resources.embedding.Embedding.create",
            _patched_endpoint(integration, _embedding_create)(openai),
        )
        wrap(
            openai,
            "api_resources.embedding.Embedding.acreate",
            _patched_endpoint_async(integration, _embedding_create)(openai),
        )

    Pin().onto(openai)
    setattr(openai, "__datadog_patch", True)


def unpatch():
    # FIXME: add unpatching. unwrapping the create methods results in a
    # >               return super().create(*args, **kwargs)
    # E               AttributeError: 'method' object has no attribute '__get__'
    pass


@trace_utils.with_traced_module
def patched_make_session(openai, pin, func, instance, args, kwargs):
    """Patch for `openai.api_requestor._make_session` which sets the service name on the
    requests session so that spans from the requests integration will use the same service
    as this integration. This is done so that the service break down will include the actual
    time spent querying the OpenAI backend.
    """
    session = func()
    cfg = config.get_from(session)
    for k, v in cfg.items():
        if k not in pin._config:
            pin._config[k] = v
    pin.clone().onto(session)
    return session


def _patched_endpoint(integration, patch_gen):
    @trace_utils.with_traced_module
    def patched_endpoint(openai, pin, func, instance, args, kwargs):
        g = patch_gen(integration, openai, pin, instance, args, kwargs)
        g.send(None)
        resp, resp_err = None, None
        try:
            resp = func(*args, **kwargs)
            return resp
        except Exception as err:
            resp_err = err
            raise
        finally:
            try:
                g.send((resp, resp_err))
            except StopIteration:
                # expected
                pass

    return patched_endpoint


def _patched_endpoint_async(integration, patch_gen):
    # Same as _patched_endpoint but async
    @trace_utils_async.with_traced_module
    async def patched_endpoint(openai, pin, func, instance, args, kwargs):
        g = patch_gen(integration, openai, pin, instance, args, kwargs)
        g.send(None)
        resp, resp_err = None, None
        try:
            resp = await func(*args, **kwargs)
            return resp
        except Exception as err:
            resp_err = err
            raise
        finally:
            try:
                g.send((resp, resp_err))
            except StopIteration:
                # expected
                pass

    return patched_endpoint


_completion_request_tag_attrs = [
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


def _completion_create(integration, openai, pin, instance, args, kwargs):
    span = integration.trace(pin, "completions", kwargs.get("model"))
    sample_pc_span = integration.is_pc_sampled_span(span)
    sample_pc_log = integration.is_pc_sampled_log(span)

    prompt = kwargs.get("prompt")
    if sample_pc_span:
        if isinstance(prompt, list):
            for idx, p in enumerate(prompt):
                span.set_tag_str("request.prompt.%d" % idx, integration.trunc(p))
        elif prompt:
            span.set_tag_str("request.prompt", integration.trunc(prompt))

    for kw_attr in _completion_request_tag_attrs:
        if kw_attr in kwargs:
            span.set_tag("request.%s" % kw_attr, kwargs[kw_attr])

    resp, error = yield span

    if error is not None:
        span.set_exc_info(*sys.exc_info())
        integration.metric(span, "incr", "request.error", 1)
    if resp and not kwargs.get("stream"):
        if "choices" in resp:
            choices = resp["choices"]
            span.set_tag("response.choices.num", len(choices))
            for choice in choices:
                idx = choice["index"]
                if "finish_reason" in choice:
                    span.set_tag_str("response.choices.%d.finish_reason" % idx, str(choice["finish_reason"]))
                if "logprobs" in choice:
                    span.set_tag("response.choices.%d.logprobs" % idx, choice["logprobs"])
                if sample_pc_span:
                    span.set_tag_str("response.choices.%d.text" % idx, integration.trunc(choice.get("text")))
        span.set_tag("response.object", resp["object"])
        for token_type in ["completion_tokens", "prompt_tokens", "total_tokens"]:
            if token_type in resp["usage"]:
                span.set_tag("response.usage.%s" % token_type, resp["usage"][token_type])
        integration.usage_metrics(span, resp.get("usage"))
        if sample_pc_log:
            integration.log(
                span,
                "info",
                "sampled completion",
                attrs={
                    "prompt": prompt,
                    "choices": resp["choices"] if resp and "choices" in resp else [],
                },
            )
    span.finish()  # TODO: try..finally to prevent memory leaks
    integration.metric(span, "dist", "request.duration", span.duration_ns)


_chat_completion_request_tag_attrs = [
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


def _chat_completion_create(integration, openai, pin, instance, args, kwargs):
    span = integration.trace(pin, "chat.completions", kwargs.get("model"))
    sample_pc_span = integration.is_pc_sampled_span(span)
    sample_pc_log = integration.is_pc_sampled_log(span)

    messages = kwargs.get("messages")
    if sample_pc_span:

        def set_message_tag(message):
            content = integration.trunc(message.get("content", ""))
            role = integration.trunc(message.get("role", ""))
            span.set_tag_str("request.messages.%d.content" % idx, content)
            span.set_tag_str("request.messages.%d.role" % idx, role)

        if isinstance(messages, list):
            for idx, message in enumerate(messages):
                set_message_tag(message)
        else:
            set_message_tag(messages)

    for kw_attr in _chat_completion_request_tag_attrs:
        if kw_attr in kwargs:
            span.set_tag("request.%s" % kw_attr, kwargs[kw_attr])

    resp, error = yield span

    completions = ""

    if error is not None:
        span.set_exc_info(*sys.exc_info())
        integration.metric(span, "incr", "request.error", 1)
    if resp and not kwargs.get("stream"):
        if "choices" in resp:
            choices = resp["choices"]
            completions = choices
            span.set_tag("response.choices.num", len(choices))
            for choice in choices:
                idx = choice["index"]
                span.set_tag_str("response.choices.%d.finish_reason" % idx, choice.get("finish_reason"))
                span.set_tag("response.choices.%d.logprobs" % idx, choice.get("logprobs"))
                if sample_pc_span and choice.get("message"):
                    span.set_tag(
                        "response.choices.%d.message.content" % idx,
                        integration.trunc(choice.get("message").get("content")),
                    )
                    span.set_tag(
                        "response.choices.%d.message.role" % idx, integration.trunc(choice.get("message").get("role"))
                    )
        span.set_tag("response.object", resp["object"])
        for token_type in ["completion_tokens", "prompt_tokens", "total_tokens"]:
            if token_type in resp["usage"]:
                span.set_tag("response.usage.%s" % token_type, resp["usage"][token_type])

        integration.usage_metrics(span, resp.get("usage"))
        if sample_pc_log:
            integration.log(
                span,
                "info" if error is None else "error",
                "sampled chat completion",
                attrs={
                    "messages": messages,
                    "completion": completions,
                },
            )
    span.finish()
    integration.metric(span, "dist", "request.duration", span.duration_ns)


def _embedding_create(integration, openai, pin, instance, args, kwargs):
    span = integration.trace(pin, "embedding", kwargs.get("model"))

    for kw_attr in ["model", "input", "user"]:
        if kw_attr in kwargs:
            if kw_attr == "input" and isinstance(kwargs["input"], list):
                for idx, inp in enumerate(kwargs["input"]):
                    span.set_tag_str("request.input.%d" % idx, integration.trunc(inp))
            span.set_tag("request.%s" % kw_attr, kwargs[kw_attr])

    resp, error = yield span

    if error is not None:
        span.set_exc_info(*sys.exc_info())
        integration.metric(span, "incr", "request.error", 1)
    if resp:
        if "data" in resp:
            span.set_tag("response.data.num-embeddings", len(resp["data"]))
            span.set_tag("response.data.embedding-length", len(resp["data"][0]["embedding"]))
        for kw_attr in ["model", "object", "usage"]:
            if kw_attr in kwargs:
                span.set_tag("response.%s" % kw_attr, kwargs[kw_attr])
        integration.usage_metrics(span, resp.get("usage"))

    span.finish()
    integration.metric(span, "dist", "request.duration", span.duration_ns)


def patched_convert(integration):
    @trace_utils.with_traced_module
    def _patched_convert(openai, pin, func, instance, args, kwargs):
        """Patch convert captures header information in the openai response"""
        for val in args:
            if isinstance(val, openai.openai_response.OpenAIResponse):
                span = pin.tracer.current_span()
                val = val._headers
                if val.get("openai-organization"):
                    org_name = val.get("openai-organization")
                    span.set_tag("organization.name", org_name)

                # Gauge total rate limit
                if val.get("x-ratelimit-limit-requests"):
                    v = val.get("x-ratelimit-limit-requests")
                    integration.metric(span, "ratelimit.requests", v, "gauge")
                if val.get("x-ratelimit-limit-tokens"):
                    v = val.get("x-ratelimit-limit-tokens")
                    integration.metric(span, "ratelimit.tokens", v, "gauge")

                # Gauge and set span info for remaining requests and tokens
                if val.get("x-ratelimit-remaining-requests"):
                    v = val.get("x-ratelimit-remaining-requests")
                    integration.metric(span, "ratelimit.remaining.requests", v, "gauge")
                    span.set_tag("organization.ratelimit.requests.remaining", v)
                if val.get("x-ratelimit-remaining-tokens"):
                    v = val.get("x-ratelimit-remaining-tokens")
                    integration.metric(span, "ratelimit.remaining.tokens", v, "gauge")
                    span.set_tag("organization.ratelimit.tokens.remaining", v)
        return func(*args, **kwargs)

    return _patched_convert
