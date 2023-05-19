import os
import re
import sys
import time
from typing import AsyncGenerator
from typing import Generator
from typing import Optional
from typing import TYPE_CHECKING

from ddtrace import config
from ddtrace.constants import SPAN_MEASURED_KEY
from ddtrace.internal.agent import get_stats_url
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.dogstatsd import get_dogstatsd_client
from ddtrace.internal.hostname import get_hostname
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.wrapping import wrap
from ddtrace.sampler import RateSampler

from .. import trace_utils
from ...pin import Pin
from ..trace_utils import set_flattened_tags
from ._logging import V2LogWriter


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

    @property
    def _user_api_key(self):
        # type: () -> Optional[str]
        """Get a representation of the user API key for tagging."""
        # Match the API key representation that OpenAI uses in their UI.
        if self._openai.api_key is None:
            return
        return "sk-...%s" % self._openai.api_key[-4:]

    def set_base_span_tags(self, span):
        # type: (Span) -> None
        span.set_tag_str(COMPONENT, self._config.integration_name)
        if self._user_api_key is not None:
            span.set_tag_str("openai.user.api_key", self._user_api_key)

        # Do these dynamically as openai users can set these at any point
        # not necessarily before patch() time.
        # organization_id is only returned by a few endpoints, grab it when we can.
        for attr in ("api_base", "api_version", "organization_id"):
            v = getattr(self._openai, attr, None)
            if v is not None:
                if attr == "organization_id":
                    span.set_tag_str("openai.organization.id", v or "")
                else:
                    span.set_tag_str(attr, v)

    def trace(self, pin, endpoint, model):
        """Start an OpenAI span.

        Set default OpenAI span attributes when possible.
        """
        resource = endpoint
        if model:
            resource += "/%s" % model
        # Reuse the service of the application as we tag the downstream requests/aiohttp spans with the service
        # `openai`. Eventually those should also be internal service spans once peer.service is implemented.
        span = pin.tracer.trace("openai.request", resource=resource, service=trace_utils.int_service(pin, self._config))
        # Enable trace metrics for these spans so users can see per-service openai usage in APM.
        span.set_tag(SPAN_MEASURED_KEY)

        self.set_base_span_tags(span)

        span.set_tag_str("openai.endpoint", endpoint)
        if model:
            span.set_tag_str("openai.model", model)
        return span

    def log(self, span, level, msg, attrs):
        if not self._config.logs_enabled:
            return
        tags = (
            "env:%s,version:%s,openai.endpoint:%s,openai.model:%s,openai.organization.name:%s,openai.user.api_key:%s"
            % (
                (config.env or ""),
                (config.version or ""),
                (span.get_tag("openai.endpoint") or ""),
                (span.get_tag("openai.model") or ""),
                (span.get_tag("openai.organization.name") or ""),
                (span.get_tag("openai.user.api_key") or ""),
            )
        )

        log = {
            "timestamp": time.time() * 1000,
            "message": msg,
            "hostname": get_hostname(),
            "ddsource": "openai",
            "service": span.service or "",
            "status": level,
            "ddtags": tags,
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
            "openai.model:%s" % (span.get_tag("openai.model") or ""),
            "openai.endpoint:%s" % (span.get_tag("openai.endpoint") or ""),
            "openai.organization.id:%s" % (span.get_tag("openai.organization.id") or ""),
            "openai.organization.name:%s" % (span.get_tag("openai.organization.name") or ""),
            "openai.user.api_key:%s" % (span.get_tag("openai.user.api_key") or ""),
            "error:%d" % span.error,
        ]
        err_type = span.get_tag("error.type")
        if err_type:
            tags.append("error_type:%s" % err_type)
        return tags

    def metric(self, span, kind, name, val, tags=None):
        """Set a metric using the OpenAI context from the given span."""
        if not self._config.metrics_enabled:
            return
        metric_tags = self._metrics_tags(span)
        if tags:
            metric_tags += tags
        if kind == "dist":
            self._statsd.distribution(name, val, tags=metric_tags)
        elif kind == "incr":
            self._statsd.increment(name, val, tags=metric_tags)
        elif kind == "gauge":
            self._statsd.gauge(name, val, tags=metric_tags)
        else:
            raise ValueError("Unexpected metric type %r" % kind)

    def record_usage(self, span, usage):
        if not usage or not self._config.metrics_enabled:
            return
        tags = self._metrics_tags(span)
        tags.append("openai.estimated:false")
        for token_type in ["prompt", "completion", "total"]:
            num_tokens = usage.get(token_type + "_tokens")
            if not num_tokens:
                continue
            span.set_tag("openai.response.usage.%s_tokens" % token_type, num_tokens)
            self._statsd.distribution("tokens.%s" % token_type, num_tokens, tags=tags)

    def trunc(self, text):
        """Truncate the given text.

        Use to avoid attaching too much data to spans.
        """
        if not text:
            return text
        text = text.replace("\n", "\\n").replace("\t", "\\t")
        if len(text) > self._config.span_char_limit:
            text = text[: self._config.span_char_limit] + "..."
        return text


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

    if hasattr(openai.api_resources, "completion"):
        _wrap_classmethod(
            openai.api_resources.completion.Completion.create,
            _patched_endpoint(openai, integration, _CompletionHook),
        )
        _wrap_classmethod(
            openai.api_resources.completion.Completion.acreate,
            _patched_endpoint_async(openai, integration, _CompletionHook),
        )

    if hasattr(openai.api_resources, "chat_completion"):
        _wrap_classmethod(
            openai.api_resources.chat_completion.ChatCompletion.create,
            _patched_endpoint(openai, integration, _ChatCompletionHook),
        )
        _wrap_classmethod(
            openai.api_resources.chat_completion.ChatCompletion.acreate,
            _patched_endpoint_async(openai, integration, _ChatCompletionHook),
        )

    if hasattr(openai.api_resources, "embedding"):
        _wrap_classmethod(
            openai.api_resources.embedding.Embedding.create,
            _patched_endpoint(openai, integration, _EmbeddingHook),
        )
        _wrap_classmethod(
            openai.api_resources.embedding.Embedding.acreate,
            _patched_endpoint_async(openai, integration, _EmbeddingHook),
        )

    setattr(openai, "__datadog_patch", True)


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
    Pin.override(session, service="openai")
    return session


def _traced_endpoint(endpoint_hook, integration, pin, args, kwargs):
    span = integration.trace(pin, args[0].OBJECT_NAME, kwargs.get("model"))
    openai_api_key = kwargs.get("api_key")
    if openai_api_key:
        # API key can either be set on the import or per request
        span.set_tag_str("openai.user.api_key", "sk-...%s" % openai_api_key[-4:])
    try:
        # Start the hook
        hook = endpoint_hook().handle_request(pin, integration, span, args, kwargs)
        hook.send(None)

        resp, error = yield

        # Record any error information
        if error is not None:
            span.set_exc_info(*sys.exc_info())
            integration.metric(span, "incr", "request.error", 1)

        # Pass the response and the error to the hook
        try:
            hook.send((resp, error))
        except StopIteration as e:
            if error is None:
                return e.value
    finally:
        # Streamed responses will be finished when the generator exits.
        if not kwargs.get("stream"):
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
                    return e.value

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
                    return e.value

    return patched_endpoint


_punc_regex = re.compile(r"[\w']+|[.,!?;~@#$%^&*()+/-]")


def _est_tokens(s):
    # type: (str) -> int
    """Provide a very rough estimate of the number of tokens.

    Approximate using the following assumptions:
        * English text
        * 1 token ~= 4 chars
        * 1 token ~= ¾ words

    Note that this function is 3x faster than tiktoken's encoding.
    """
    est1 = len(s) / 4
    est2 = len(_punc_regex.findall(s)) * 0.75
    est = round((1.5 * est1 + 0.5 * est2) / 2)
    return est


class _EndpointHook:
    def handle_request(self, pin, integration, span, args, kwargs):
        raise NotImplementedError


class _BaseCompletionHook(_EndpointHook):
    """Share common logic between Completion and ChatCompletion endpoints
    related to span processing and recording metrics.
    """

    _request_tag_attrs = []

    def _record_request(self, span, kwargs):
        for kw_attr in self._request_tag_attrs:
            if kw_attr in kwargs:
                if isinstance(kwargs[kw_attr], dict):
                    set_flattened_tags(
                        span, [("openai.request.{}.{}".format(kw_attr, k), v) for k, v in kwargs[kw_attr].items()]
                    )
                else:
                    span.set_tag("openai.request.%s" % kw_attr, kwargs[kw_attr])

    def _handle_response(self, pin, span, integration, resp):
        """Handle the response object returned from endpoint calls.

        This method helps with streamed responses by wrapping the generator returned with a
        generator that traces the reading of the response.
        """

        def shared_gen():
            try:
                num_prompt_tokens = span.get_metric("openai.response.usage.prompt_tokens") or 0
                num_completion_tokens = yield

                span.set_metric("openai.response.usage.completion_tokens", num_completion_tokens)
                total_tokens = num_prompt_tokens + num_completion_tokens
                span.set_metric("openai.response.usage.total_tokens", total_tokens)
                integration.metric(span, "dist", "tokens.prompt", num_prompt_tokens, tags=["openai.estimated:true"])
                integration.metric(
                    span, "dist", "tokens.completion", num_completion_tokens, tags=["openai.estimated:true"]
                )
                integration.metric(span, "dist", "tokens.total", total_tokens, tags=["openai.estimated:true"])
            finally:
                span.finish()
                integration.metric(span, "dist", "request.duration", span.duration_ns)

        # A chunk corresponds to a token:
        #  https://community.openai.com/t/how-to-get-total-tokens-from-a-stream-of-completioncreaterequests/110700
        #  https://community.openai.com/t/openai-api-get-usage-tokens-in-response-when-set-stream-true/141866
        if isinstance(resp, AsyncGenerator):

            async def traced_streamed_response():
                g = shared_gen()
                g.send(None)
                num_completion_tokens = 0
                try:
                    async for chunk in resp:
                        num_completion_tokens += 1
                        yield chunk
                finally:
                    try:
                        g.send(num_completion_tokens)
                    except StopIteration:
                        pass

            return traced_streamed_response()

        elif isinstance(resp, Generator):

            def traced_streamed_response():
                g = shared_gen()
                g.send(None)
                num_completion_tokens = 0
                try:
                    for chunk in resp:
                        num_completion_tokens += 1
                        yield chunk
                finally:
                    try:
                        g.send(num_completion_tokens)
                    except StopIteration:
                        pass

            return traced_streamed_response()

        return resp


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

    def handle_request(self, pin, integration, span, args, kwargs):
        sample_pc_span = integration.is_pc_sampled_span(span)

        if sample_pc_span:
            prompt = kwargs.get("prompt", "")
            if isinstance(prompt, str):
                span.set_tag_str("openai.request.prompt", integration.trunc(prompt))
            elif prompt:
                for idx, p in enumerate(prompt):
                    span.set_tag_str("openai.request.prompt.%d" % idx, integration.trunc(p))

        if "stream" in kwargs and kwargs["stream"]:
            prompt = kwargs.get("prompt", "")
            num_prompt_tokens = 0
            if isinstance(prompt, str):
                num_prompt_tokens += _est_tokens(prompt)
            else:
                for p in prompt:
                    num_prompt_tokens += _est_tokens(p)
            span.set_metric("openai.response.usage.prompt_tokens", num_prompt_tokens)

        self._record_request(span, kwargs)

        resp, error = yield

        if resp and not kwargs.get("stream"):
            if "choices" in resp:
                choices = resp["choices"]
                span.set_tag("openai.response.choices.num", len(choices))
                for choice in choices:
                    idx = choice["index"]
                    if "finish_reason" in choice:
                        span.set_tag_str("openai.response.choices.%d.finish_reason" % idx, str(choice["finish_reason"]))
                    if "logprobs" in choice:
                        span.set_tag_str("openai.response.choices.%d.logprobs" % idx, "returned")
                    if sample_pc_span:
                        span.set_tag_str("openai.response.choices.%d.text" % idx, integration.trunc(choice.get("text")))
            span.set_tag("openai.response.object", resp["object"])
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
        return self._handle_response(pin, span, integration, resp)


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

    def handle_request(self, pin, integration, span, args, kwargs):
        sample_pc_span = integration.is_pc_sampled_span(span)
        messages = kwargs.get("messages")
        if sample_pc_span and messages:
            for idx, m in enumerate(messages):
                content = integration.trunc(m.get("content", ""))
                role = integration.trunc(m.get("role", ""))
                span.set_tag_str("openai.request.messages.%d.content" % idx, content)
                span.set_tag_str("openai.request.messages.%d.role" % idx, role)

        if "stream" in kwargs and kwargs["stream"]:
            # streamed responses do not have a usage field, so we have to
            # estimate the number of tokens returned.
            est_num_message_tokens = 0
            for m in messages:
                est_num_message_tokens += _est_tokens(m.get("content", ""))
            span.set_metric("openai.response.usage.prompt_tokens", est_num_message_tokens)

        self._record_request(span, kwargs)

        resp, error = yield

        if resp and not kwargs.get("stream"):
            choices = resp.get("choices", [])
            for choice in choices:
                idx = choice["index"]
                span.set_tag_str("openai.response.choices.%d.finish_reason" % idx, choice.get("finish_reason"))
                if sample_pc_span and choice.get("message"):
                    span.set_tag(
                        "openai.response.choices.%d.message.content" % idx,
                        integration.trunc(choice.get("message").get("content")),
                    )
                    span.set_tag(
                        "openai.response.choices.%d.message.role" % idx,
                        integration.trunc(choice.get("message").get("role")),
                    )
            span.set_tag("openai.response.object", resp["object"])
            integration.record_usage(span, resp.get("usage"))

            if integration.is_pc_sampled_log(span):
                messages = kwargs.get("messages")
                integration.log(
                    span,
                    "info" if error is None else "error",
                    "sampled chat completion",
                    attrs={
                        "messages": messages,
                        "completion": choices,
                    },
                )
        return self._handle_response(pin, span, integration, resp)


class _EmbeddingHook(_EndpointHook):
    def handle_request(self, pin, integration, span, args, kwargs):
        for kw_attr in ["model", "input", "user"]:
            if kw_attr in kwargs:
                if kw_attr == "input" and integration.is_pc_sampled_span(span):
                    if isinstance(kwargs["input"], list):
                        for idx, inp in enumerate(kwargs["input"]):
                            span.set_tag_str("openai.request.input.%d" % idx, integration.trunc(str(inp)))
                    else:
                        span.set_tag("openai.request.%s" % kw_attr, kwargs[kw_attr])
                else:
                    span.set_tag("openai.request.%s" % kw_attr, kwargs[kw_attr])

        resp, error = yield

        if resp:
            if "data" in resp:
                span.set_tag("openai.response.data.num-embeddings", len(resp["data"]))
                span.set_tag("openai.response.data.embedding-length", len(resp["data"][0]["embedding"]))
            if "object" in kwargs:
                span.set_tag("openai.response.%s" % kw_attr, kwargs[kw_attr])
            integration.record_usage(span, resp.get("usage"))
        return resp


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
            span.set_tag("openai.organization.name", org_name)

        # Gauge total rate limit
        if val.get("x-ratelimit-limit-requests"):
            v = val.get("x-ratelimit-limit-requests")
            if v is not None:
                integration.metric(span, "gauge", "ratelimit.requests", v)
        if val.get("x-ratelimit-limit-tokens"):
            v = val.get("x-ratelimit-limit-tokens")
            if v is not None:
                integration.metric(span, "gauge", "ratelimit.tokens", v)

        # Gauge and set span info for remaining requests and tokens
        if val.get("x-ratelimit-remaining-requests"):
            v = val.get("x-ratelimit-remaining-requests")
            if v is not None:
                integration.metric(span, "gauge", "ratelimit.remaining.requests", v)
                span.set_tag("openai.organization.ratelimit.requests.remaining", v)
        if val.get("x-ratelimit-remaining-tokens"):
            v = val.get("x-ratelimit-remaining-tokens")
            if v is not None:
                integration.metric(span, "gauge", "ratelimit.remaining.tokens", v)
                span.set_tag("openai.organization.ratelimit.tokens.remaining", v)
        return func(*args, **kwargs)

    return patched_convert
