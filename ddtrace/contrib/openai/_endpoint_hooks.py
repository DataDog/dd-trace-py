from typing import AsyncGenerator
from typing import Generator

from ..trace_utils import set_flattened_tags
from .utils import _est_tokens


class _EndpointHook:
    _request_tag_attrs = []
    _default_name = "openai"

    def _record_request(self, span, kwargs):
        for kw_attr in self._request_tag_attrs:
            if kw_attr in kwargs:
                if isinstance(kwargs[kw_attr], dict):
                    set_flattened_tags(
                        span, [("openai.request.{}.{}".format(kw_attr, k), v) for k, v in kwargs[kw_attr].items()]
                    )
                else:
                    span.set_tag("openai.request.%s" % kw_attr, kwargs[kw_attr])

    def handle_request(self, pin, integration, span, args, kwargs):
        self._pre_response(pin, integration, span, args, kwargs)
        self._record_request(span, kwargs)
        resp, error = yield
        return self._post_response(pin, integration, span, args, kwargs, resp, error)

    def _pre_response(self, pin, integration, span, args, kwargs):
        raise NotImplementedError

    def _post_response(self, pin, integration, span, args, kwargs, resp, error):
        raise NotImplementedError


class _BaseCompletionHook(_EndpointHook):
    """
    Share streamed response handling logic between Completion and ChatCompletion endpoints.
    """

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
    _default_name = "completions"

    def _pre_response(self, pin, integration, span, args, kwargs):
        if integration.is_pc_sampled_span(span):
            prompt = kwargs.get("prompt", "")
            if isinstance(prompt, str):
                span.set_tag_str("openai.request.prompt", integration.trunc(prompt))
            elif prompt:
                for idx, p in enumerate(prompt):
                    span.set_tag_str("openai.request.prompt.%d" % idx, integration.trunc(p))

        if kwargs.get("stream"):
            prompt = kwargs.get("prompt", "")
            num_prompt_tokens = 0
            if isinstance(prompt, str):
                num_prompt_tokens += _est_tokens(prompt)
            else:
                for p in prompt:
                    num_prompt_tokens += _est_tokens(p)
            span.set_metric("openai.response.usage.prompt_tokens", num_prompt_tokens)

        return

    def _post_response(self, pin, integration, span, args, kwargs, resp, error):
        if not resp or kwargs.get("stream"):
            return self._handle_response(pin, span, integration, resp)
        if "choices" in resp:
            choices = resp["choices"]
            span.set_tag("openai.response.choices.num", len(choices))
            for choice in choices:
                idx = choice["index"]
                if "finish_reason" in choice:
                    span.set_tag_str("openai.response.choices.%d.finish_reason" % idx, str(choice["finish_reason"]))
                if "logprobs" in choice:
                    span.set_tag_str("openai.response.choices.%d.logprobs" % idx, "returned")
                if integration.is_pc_sampled_span(span):
                    span.set_tag_str("openai.response.choices.%d.text" % idx, integration.trunc(choice.get("text")))
        span.set_tag("openai.response.object", resp["object"])
        integration.record_usage(span, resp.get("usage"))
        if integration.is_pc_sampled_log(span):
            prompt = kwargs.get("prompt", "")
            integration.log(
                span,
                "info" if error is None else "error",
                "sampled %s" % self._default_name,
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
    _default_name = "chat.completions"

    def _pre_response(self, pin, integration, span, args, kwargs):
        messages = kwargs.get("messages", [])
        if integration.is_pc_sampled_span(span):
            for idx, m in enumerate(messages):
                content = integration.trunc(m.get("content", ""))
                role = integration.trunc(m.get("role", ""))
                span.set_tag_str("openai.request.messages.%d.content" % idx, content)
                span.set_tag_str("openai.request.messages.%d.role" % idx, role)

        if kwargs.get("stream"):
            # streamed responses do not have a usage field, so we have to
            # estimate the number of tokens returned.
            est_num_message_tokens = 0
            for m in messages:
                est_num_message_tokens += _est_tokens(m.get("content", ""))
            span.set_metric("openai.response.usage.prompt_tokens", est_num_message_tokens)

        return

    def _post_response(self, pin, integration, span, args, kwargs, resp, error):
        if not resp or kwargs.get("stream"):
            return self._handle_response(pin, span, integration, resp)
        choices = resp.get("choices", [])
        for choice in choices:
            idx = choice["index"]
            span.set_tag_str("openai.response.choices.%d.finish_reason" % idx, choice.get("finish_reason"))
            if integration.is_pc_sampled_span(span) and choice.get("message"):
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
                "sampled %s" % self._default_name,
                attrs={
                    "messages": messages,
                    "completion": choices,
                },
            )
        return self._handle_response(pin, span, integration, resp)


class _EmbeddingHook(_EndpointHook):
    _request_tag_attrs = ["model", "user"]
    _default_name = "embeddings"

    def _pre_response(self, pin, integration, span, args, kwargs):
        embedding_input = kwargs.get("input", "")
        if integration.is_pc_sampled_span(span):
            if isinstance(embedding_input, list):
                for idx, inp in enumerate(embedding_input):
                    span.set_tag_str("openai.request.input.%d" % idx, integration.trunc(str(inp)))
            else:
                span.set_tag("openai.request.input", embedding_input)
        return

    def _post_response(self, pin, integration, span, args, kwargs, resp, error):
        if resp:
            if "data" in resp:
                span.set_tag("openai.response.data.num-embeddings", len(resp["data"]))
                span.set_tag("openai.response.data.embedding-length", len(resp["data"][0]["embedding"]))
            integration.record_usage(span, resp.get("usage"))
        return resp
