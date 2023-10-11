from typing import AsyncGenerator
from typing import Generator

from .utils import _compute_prompt_token_count
from .utils import _format_openai_api_key


API_VERSION = "v1"


class _EndpointHook:
    """
    Base class for all OpenAI endpoint hooks.
    Each new endpoint hook should declare `_request_arg_params` and `_request_kwarg_params`,
    which will be tagged automatically by _EndpointHook._record_request().
    For endpoint-specific request/response parameters that requires special casing, add that logic to
    the endpoint hook's `_record_request()` after a super call to the base `_EndpointHook._record_request()`.
    """

    # _request_arg_params must include the names of arg parameters in order.
    # If a given arg requires special casing, replace with `None` to avoid automatic tagging.
    _request_arg_params = ()
    # _request_kwarg_params must include the names of kwarg parameters to tag automatically.
    # If a given kwarg requires special casing, remove from this tuple to avoid automatic tagging.
    _request_kwarg_params = ()
    _base_level_tag_args = ("api_base", "api_type", "api_version")
    ENDPOINT_NAME = "openai"
    HTTP_METHOD_TYPE = ""
    OPERATION_ID = ""  # Each endpoint hook must provide an operationID as specified in the OpenAI API specs:
    # https://raw.githubusercontent.com/openai/openai-openapi/master/openapi.yaml

    def _record_request(self, pin, integration, span, args, kwargs):
        """
        Set base-level openai tags, as well as request params from args and kwargs.
        All inherited EndpointHook classes should include a super call to this method before performing
        endpoint-specific request tagging logic.
        """
        endpoint = self.ENDPOINT_NAME
        if endpoint is None:
            endpoint = "%s" % args[0].OBJECT_NAME
        span.set_tag_str("openai.request.endpoint", "/%s/%s" % (API_VERSION, endpoint))
        span.set_tag_str("openai.request.method", self.HTTP_METHOD_TYPE)

        if self._request_arg_params and len(self._request_arg_params) > 1:
            for idx, arg in enumerate(self._request_arg_params, 1):
                if idx >= len(args):
                    break
                if arg is None or args[idx] is None:
                    continue
                if arg in self._base_level_tag_args:
                    span.set_tag_str("openai.%s" % arg, str(args[idx]))
                elif arg == "organization":
                    span.set_tag_str("openai.organization.id", args[idx])
                elif arg == "api_key":
                    span.set_tag_str("openai.user.api_key", _format_openai_api_key(args[idx]))
                else:
                    span.set_tag_str("openai.request.%s" % arg, str(args[idx]))
        for kw_attr in self._request_kwarg_params:
            if kw_attr not in kwargs:
                continue
            if isinstance(kwargs[kw_attr], dict):
                for k, v in kwargs[kw_attr].items():
                    span.set_tag_str("openai.request.%s.%s" % (kw_attr, k), str(v))
            else:
                span.set_tag_str("openai.request.%s" % kw_attr, str(kwargs[kw_attr]))

    def handle_request(self, pin, integration, span, args, kwargs):
        self._record_request(pin, integration, span, args, kwargs)
        resp, error = yield
        return self._record_response(pin, integration, span, args, kwargs, resp, error)

    def _record_response(self, pin, integration, span, args, kwargs, resp, error):
        raise NotImplementedError


class _BaseCompletionHook(_EndpointHook):
    """
    Share streamed response handling logic between Completion and ChatCompletion endpoints.
    """

    _request_arg_params = ("api_key", "api_base", "api_type", "request_id", "api_version", "organization")

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
                if span.get_metric("openai.request.prompt_tokens_estimated") == 0:
                    integration.metric(span, "dist", "tokens.prompt", num_prompt_tokens)
                else:
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
    _request_kwarg_params = (
        "model",
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
    )
    ENDPOINT_NAME = "completions"
    HTTP_METHOD_TYPE = "POST"
    OPERATION_ID = "createCompletion"

    def _record_request(self, pin, integration, span, args, kwargs):
        super()._record_request(pin, integration, span, args, kwargs)
        prompt = kwargs.get("prompt", "")
        if integration.is_pc_sampled_span(span):
            if isinstance(prompt, str):
                span.set_tag_str("openai.request.prompt", integration.trunc(prompt))
            elif prompt:
                for idx, p in enumerate(prompt):
                    span.set_tag_str("openai.request.prompt.%d" % idx, integration.trunc(str(p)))
        return

    def _record_response(self, pin, integration, span, args, kwargs, resp, error):
        if not resp:
            return self._handle_response(pin, span, integration, resp)
        prompt = kwargs.get("prompt", "")
        if kwargs.get("stream"):
            num_prompt_tokens = 0
            estimated = False
            if isinstance(prompt, str) or isinstance(prompt, list) and isinstance(prompt[0], int):
                estimated, prompt_tokens = _compute_prompt_token_count(prompt, kwargs.get("model"))
                num_prompt_tokens += prompt_tokens
            else:
                for p in prompt:
                    estimated, prompt_tokens = _compute_prompt_token_count(p, kwargs.get("model"))
                    num_prompt_tokens += prompt_tokens
            span.set_metric("openai.request.prompt_tokens_estimated", int(estimated))
            span.set_metric("openai.response.usage.prompt_tokens", num_prompt_tokens)
            return self._handle_response(pin, span, integration, resp)
        if "choices" in resp:
            choices = resp["choices"]
            span.set_metric("openai.response.choices_count", len(choices))
            span.set_metric("openai.response.created", resp.get("created"))
            span.set_tag_str("openai.response.id", resp.get("id", ""))
            span.set_tag_str("openai.response.model", resp.get("model", ""))
            for choice in choices:
                idx = choice["index"]
                if "finish_reason" in choice:
                    span.set_tag_str("openai.response.choices.%d.finish_reason" % idx, str(choice["finish_reason"]))
                if "logprobs" in choice:
                    span.set_tag_str("openai.response.choices.%d.logprobs" % idx, "returned")
                if integration.is_pc_sampled_span(span):
                    span.set_tag_str("openai.response.choices.%d.text" % idx, integration.trunc(choice.get("text")))
        integration.record_usage(span, resp.get("usage"))
        if integration.is_pc_sampled_log(span):
            integration.log(
                span,
                "info" if error is None else "error",
                "sampled %s" % self.OPERATION_ID,
                attrs={
                    "prompt": prompt,
                    "choices": resp["choices"] if resp and "choices" in resp else [],
                },
            )
        return self._handle_response(pin, span, integration, resp)


class _ChatCompletionHook(_BaseCompletionHook):
    _request_kwarg_params = (
        "model",
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
    )
    ENDPOINT_NAME = "chat/completions"
    HTTP_METHOD_TYPE = "POST"
    OPERATION_ID = "createChatCompletion"

    def _record_request(self, pin, integration, span, args, kwargs):
        super()._record_request(pin, integration, span, args, kwargs)
        messages = kwargs.get("messages", [])
        if integration.is_pc_sampled_span(span):
            for idx, m in enumerate(messages):
                content = integration.trunc(m.get("content", ""))
                role = integration.trunc(m.get("role", ""))
                name = integration.trunc(m.get("name", ""))
                span.set_tag_str("openai.request.messages.%d.content" % idx, content)
                span.set_tag_str("openai.request.messages.%d.role" % idx, role)
                span.set_tag_str("openai.request.messages.%d.name" % idx, name)
        return

    def _record_response(self, pin, integration, span, args, kwargs, resp, error):
        if not resp:
            return self._handle_response(pin, span, integration, resp)
        messages = kwargs.get("messages")
        if kwargs.get("stream"):
            est_num_message_tokens = 0
            estimated = False
            for m in messages:
                estimated, prompt_tokens = _compute_prompt_token_count(m.get("content", ""), kwargs.get("model"))
                est_num_message_tokens += prompt_tokens
            span.set_metric("openai.request.prompt_tokens_estimated", int(estimated))
            span.set_metric("openai.response.usage.prompt_tokens", est_num_message_tokens)
            return self._handle_response(pin, span, integration, resp)
        choices = resp.get("choices", [])
        span.set_metric("openai.response.choices_count", len(choices))
        span.set_metric("openai.response.created", resp.get("created"))
        span.set_tag_str("openai.response.id", resp.get("id", ""))
        span.set_tag_str("openai.response.model", resp.get("model", ""))
        for choice in choices:
            idx = choice["index"]
            span.set_tag_str("openai.response.choices.%d.finish_reason" % idx, choice.get("finish_reason"))
            if integration.is_pc_sampled_span(span) and choice.get("message"):
                span.set_tag_str(
                    "openai.response.choices.%d.message.content" % idx,
                    integration.trunc(choice.get("message", {}).get("content", "")),
                )
                span.set_tag_str(
                    "openai.response.choices.%d.message.role" % idx,
                    integration.trunc(choice.get("message", {}).get("role", "")),
                )
                span.set_tag_str(
                    "openai.response.choices.%d.message.name" % idx,
                    integration.trunc(choice.get("message", {}).get("name", "")),
                )
        integration.record_usage(span, resp.get("usage"))
        if integration.is_pc_sampled_log(span):
            integration.log(
                span,
                "info" if error is None else "error",
                "sampled %s" % self.OPERATION_ID,
                attrs={
                    "messages": messages,
                    "completion": choices,
                },
            )
        return self._handle_response(pin, span, integration, resp)


class _EmbeddingHook(_EndpointHook):
    _request_arg_params = ("api_key", "api_base", "api_type", "request_id", "api_version", "organization")
    _request_kwarg_params = ("model", "user")
    ENDPOINT_NAME = "embeddings"
    HTTP_METHOD_TYPE = "POST"
    OPERATION_ID = "createEmbedding"

    def _record_request(self, pin, integration, span, args, kwargs):
        """
        Embedding endpoint allows multiple inputs, each of which we specify a request tag for, so have to
        manually set them in _pre_response().
        """
        super()._record_request(pin, integration, span, args, kwargs)
        embedding_input = kwargs.get("input", "")
        if not integration.is_pc_sampled_span(span):
            return
        if isinstance(embedding_input, list) and not isinstance(embedding_input[0], int):
            for idx, inp in enumerate(embedding_input):
                span.set_tag_str("openai.request.input.%d" % idx, integration.trunc(str(inp)))
        else:
            span.set_tag_str("openai.request.input", integration.trunc(str(embedding_input)))
        return

    def _record_response(self, pin, integration, span, args, kwargs, resp, error):
        if not resp:
            return
        span.set_tag_str("openai.response.model", resp.get("model", ""))
        if "data" in resp:
            span.set_metric("openai.response.embeddings_count", len(resp["data"]))
            for result in resp["data"]:
                idx = result["index"]
                span.set_metric("openai.response.embeddings.%d.embedding-length" % idx, len(result["embedding"]))
        integration.record_usage(span, resp.get("usage"))
        return resp


class _ListHook(_EndpointHook):
    """
    Hook for openai.ListableAPIResource, which is used by Model.list, File.list, and FineTune.list.
    """

    _request_arg_params = ("api_key", "request_id", "api_version", "organization", "api_base", "api_type")
    _request_kwarg_params = ("user",)
    ENDPOINT_NAME = None
    HTTP_METHOD_TYPE = "GET"
    OPERATION_ID = "list"

    def _record_request(self, pin, integration, span, args, kwargs):
        super()._record_request(pin, integration, span, args, kwargs)
        endpoint = span.get_tag("openai.request.endpoint")
        if endpoint.endswith("/models"):
            span.resource = "listModels"
        elif endpoint.endswith("/files"):
            span.resource = "listFiles"
        elif endpoint.endswith("/fine-tunes"):
            span.resource = "listFineTunes"
        return

    def _record_response(self, pin, integration, span, args, kwargs, resp, error):
        if not resp:
            return
        span.set_metric("openai.response.count", len(resp.get("data", [])))
        return resp


class _RetrieveHook(_EndpointHook):
    """Hook for openai.APIResource, which is used by Model.retrieve, File.retrieve, and FineTune.retrieve."""

    _request_arg_params = (None, "api_key", "request_id", "request_timeout")
    _request_kwarg_params = ("user",)
    _response_attrs = (
        "id",
        "owned_by",
        "model",
        "parent",
        "root",
        "bytes",
        "created_at",
        "purpose",
        "filename",
        "fine_tuned_model",
        "status",
        "status_details",
        "updated_at",
    )
    ENDPOINT_NAME = None
    HTTP_METHOD_TYPE = "GET"
    OPERATION_ID = "retrieve"

    def _record_request(self, pin, integration, span, args, kwargs):
        super()._record_request(pin, integration, span, args, kwargs)
        endpoint = span.get_tag("openai.request.endpoint")
        if endpoint.endswith("/models"):
            span.resource = "retrieveModel"
            span.set_tag_str("openai.request.model", args[1])
        elif endpoint.endswith("/files"):
            span.resource = "retrieveFile"
            span.set_tag_str("openai.request.file_id", args[1])
        elif endpoint.endswith("/fine-tunes"):
            span.resource = "retrieveFineTune"
            span.set_tag_str("openai.request.fine_tune_id", args[1])
        span.set_tag_str("openai.request.endpoint", "%s/*" % endpoint)
        return

    def _record_response(self, pin, integration, span, args, kwargs, resp, error):
        if not resp:
            return

        for resp_attr in self._response_attrs:
            if resp_attr in resp:
                span.set_tag_str("openai.response.%s" % resp_attr, str(resp.get(resp_attr, "")))
        if resp.get("permission"):
            for k, v in resp.get("permission", [])[0].items():
                if k == "object":  # object does not contain useful information
                    continue
                if isinstance(v, str):
                    span.set_tag_str("openai.response.permission.%s" % k, v)
                elif isinstance(v, bool) or isinstance(v, int):
                    span.set_metric("openai.response.permission.%s" % k, int(v))
                else:
                    span.set_tag_str("openai.response.permission.%s" % k, str(v))
        for k, v in resp.get("hyperparams", {}).items():
            span.set_tag_str("openai.response.hyperparams.%s" % k, str(v))
        for resp_attr in ("result_files", "training_files", "validation_files"):
            if resp_attr in resp:
                span.set_metric("openai.response.%s_count" % resp_attr, len(resp.get(resp_attr, [])))
        if resp.get("events"):
            span.set_metric("openai.response.events_count", len(resp.get("events", [])))
        return resp


class _DeleteHook(_EndpointHook):
    """Hook for openai.DeletableAPIResource, which is used by File.delete, and Model.delete."""

    _request_arg_params = (None, "api_type", "api_version")
    _request_kwarg_params = ("user",)
    ENDPOINT_NAME = None
    HTTP_METHOD_TYPE = "DELETE"
    OPERATION_ID = "delete"

    def _record_request(self, pin, integration, span, args, kwargs):
        super()._record_request(pin, integration, span, args, kwargs)
        endpoint = span.get_tag("openai.request.endpoint")
        if endpoint.endswith("/models"):
            span.resource = "deleteModel"
            span.set_tag_str("openai.request.model", args[1])
        elif endpoint.endswith("/files"):
            span.resource = "deleteFile"
            span.set_tag_str("openai.request.file_id", args[1])
        span.set_tag_str("openai.request.endpoint", "%s/*" % endpoint)
        return

    def _record_response(self, pin, integration, span, args, kwargs, resp, error):
        if not resp:
            return
        if hasattr(resp, "data"):
            if resp._headers.get("openai-organization"):
                span.set_tag_str("openai.organization.name", resp._headers.get("openai-organization"))
            span.set_tag_str("openai.response.id", resp.data.get("id", ""))
            span.set_tag_str("openai.response.deleted", str(resp.data.get("deleted", "")))
        else:
            span.set_tag_str("openai.response.id", resp.get("id", ""))
            span.set_tag_str("openai.response.deleted", str(resp.get("deleted", "")))
        return resp


class _EditHook(_EndpointHook):
    _request_arg_params = ("api_key", "api_base", "api_type", "request_id", "api_version", "organization")
    _request_kwarg_params = ("model", "n", "temperature", "top_p", "user")
    ENDPOINT_NAME = "edits"
    HTTP_METHOD_TYPE = "POST"
    OPERATION_ID = "createEdit"

    def _record_request(self, pin, integration, span, args, kwargs):
        super()._record_request(pin, integration, span, args, kwargs)
        if integration.is_pc_sampled_span(span):
            instruction = kwargs.get("instruction", "")
            input_text = kwargs.get("input", "")
            span.set_tag_str("openai.request.instruction", integration.trunc(instruction))
            span.set_tag_str("openai.request.input", integration.trunc(input_text))
        return

    def _record_response(self, pin, integration, span, args, kwargs, resp, error):
        if not resp:
            return
        choices = resp.get("choices", [])
        span.set_metric("openai.response.choices_count", len(choices))
        span.set_metric("openai.response.created", resp.get("created"))
        if integration.is_pc_sampled_span(span):
            for choice in choices:
                idx = choice["index"]
                span.set_tag_str("openai.response.choices.%d.text" % idx, integration.trunc(choice.get("text")))
        integration.record_usage(span, resp.get("usage"))
        if integration.is_pc_sampled_log(span):
            instruction = kwargs.get("instruction", "")
            input_text = kwargs.get("input", "")
            integration.log(
                span,
                "info" if error is None else "error",
                "sampled %s" % self.OPERATION_ID,
                attrs={
                    "instruction": instruction,
                    "input": input_text,
                    "choices": resp["choices"] if resp and "choices" in resp else [],
                },
            )
        return resp


class _ImageHook(_EndpointHook):
    ENDPOINT_NAME = "images"
    HTTP_METHOD_TYPE = "POST"

    def _record_request(self, pin, integration, span, args, kwargs):
        super()._record_request(pin, integration, span, args, kwargs)
        span.set_tag_str("openai.request.model", "dall-e")

    def _record_response(self, pin, integration, span, args, kwargs, resp, error):
        if not resp:
            return
        if integration.is_pc_sampled_span(span):
            choices = resp.get("data", [])
            span.set_metric("openai.response.created", resp.get("created"))
            span.set_metric("openai.response.images_count", len(choices))
            if integration.is_pc_sampled_span(span):
                for idx, choice in enumerate(choices):
                    if choice.get("b64_json"):
                        span.set_tag_str("openai.response.images.%d.b64_json" % idx, "returned")
                    else:
                        span.set_tag_str("openai.response.images.%d.url" % idx, integration.trunc(choice.get("url")))
        if integration.is_pc_sampled_log(span):
            attrs_dict = {}
            if kwargs.get("response_format", "") == "b64_json":
                attrs_dict.update({"choices": [{"b64_json": "returned"} for _ in resp.get("data", [])]})
            else:
                attrs_dict.update({"choices": resp["data"] if resp and "data" in resp else []})
            if "prompt" in self._request_kwarg_params:
                attrs_dict.update({"prompt": kwargs.get("prompt", "")})
            if "image" in self._request_kwarg_params:
                image = args[1] or ""
                attrs_dict.update({"image": getattr(image, "name", "").split("/")[-1]})
            if "mask" in self._request_kwarg_params:
                mask = args[2] or ""
                attrs_dict.update({"mask": getattr(mask, "name", "").split("/")[-1]})
            integration.log(
                span, "info" if error is None else "error", "sampled %s" % self.OPERATION_ID, attrs=attrs_dict
            )
        return resp


class _ImageCreateHook(_ImageHook):
    _request_arg_params = ("api_key", "api_base", "api_type", "api_version", "organization")
    _request_kwarg_params = ("prompt", "n", "size", "response_format", "user")
    ENDPOINT_NAME = "images/generations"
    OPERATION_ID = "createImage"


class _ImageEditHook(_ImageHook):
    _request_arg_params = (None, None, "api_key", "api_base", "api_type", "api_version", "organization")
    _request_kwarg_params = ("prompt", "n", "size", "response_format", "user", "image", "mask")
    ENDPOINT_NAME = "images/edits"
    OPERATION_ID = "createImageEdit"

    def _record_request(self, pin, integration, span, args, kwargs):
        super()._record_request(pin, integration, span, args, kwargs)
        if not integration.is_pc_sampled_span:
            return
        image = args[1]
        mask = args[2]
        if image:
            if hasattr(image, "name"):
                span.set_tag_str("openai.request.image", integration.trunc(image.name.split("/")[-1]))
            else:
                span.set_tag_str("openai.request.image", "")
        if mask:
            if hasattr(mask, "name"):
                span.set_tag_str("openai.request.mask", integration.trunc(mask.name.split("/")[-1]))
            else:
                span.set_tag_str("openai.request.mask", "")
        return


class _ImageVariationHook(_ImageHook):
    _request_arg_params = (None, "api_key", "api_base", "api_type", "api_version", "organization")
    _request_kwarg_params = ("n", "size", "response_format", "user", "image")
    ENDPOINT_NAME = "images/variations"
    OPERATION_ID = "createImageVariation"

    def _record_request(self, pin, integration, span, args, kwargs):
        super()._record_request(pin, integration, span, args, kwargs)
        if not integration.is_pc_sampled_span:
            return
        image = args[1]
        if image:
            if hasattr(image, "name"):
                span.set_tag_str("openai.request.image", integration.trunc(image.name.split("/")[-1]))
            else:
                span.set_tag_str("openai.request.image", "")
        return


class _BaseAudioHook(_EndpointHook):
    _request_arg_params = ("model", None, "api_key", "api_base", "api_type", "api_version", "organization")
    ENDPOINT_NAME = "audio"
    HTTP_METHOD_TYPE = "POST"

    def _record_request(self, pin, integration, span, args, kwargs):
        super()._record_request(pin, integration, span, args, kwargs)
        if not integration.is_pc_sampled_span:
            return
        audio_file = args[2]
        if audio_file and hasattr(audio_file, "name"):
            span.set_tag_str("openai.request.filename", integration.trunc(audio_file.name.split("/")[-1]))
        else:
            span.set_tag_str("openai.request.filename", "")

    def _record_response(self, pin, integration, span, args, kwargs, resp, error):
        if not resp:
            return
        if integration.is_pc_sampled_span(span):
            if isinstance(resp, str):
                text = resp
            elif isinstance(resp, dict):
                text = resp.get("text", "")
                if resp.get("language"):
                    span.set_tag_str("openai.response.language", resp.get("language"))
                if resp.get("duration"):
                    span.set_metric("openai.response.duration", resp.get("duration"))
                if resp.get("segments", []):
                    span.set_metric("openai.response.segments_count", len(resp["segments"]))
            else:
                text = ""
            span.set_tag_str("openai.response.text", integration.trunc(text))
        if integration.is_pc_sampled_log(span):
            file_input = args[2] or ""
            integration.log(
                span,
                "info" if error is None else "error",
                "sampled %s" % self.OPERATION_ID,
                attrs={
                    "file": getattr(file_input, "name", "").split("/")[-1],
                    "prompt": kwargs.get("prompt", ""),
                    "language": kwargs.get("language", ""),
                    "text": resp["text"] if isinstance(resp, dict) else resp,
                },
            )
        return resp


class _AudioTranscriptionHook(_BaseAudioHook):
    _request_kwarg_params = (
        "prompt",
        "response_format",
        "temperature",
        "language",
        "user",
    )
    ENDPOINT_NAME = "audio/transcriptions"
    OPERATION_ID = "createTranscription"


class _AudioTranslationHook(_BaseAudioHook):
    _request_kwarg_params = (
        "prompt",
        "response_format",
        "temperature",
        "user",
    )
    ENDPOINT_NAME = "audio/translations"
    OPERATION_ID = "createTranslation"


class _ModerationHook(_EndpointHook):
    _request_arg_params = ("input", "model", "api_key")
    ENDPOINT_NAME = "moderations"
    HTTP_METHOD_TYPE = "POST"
    OPERATION_ID = "createModeration"

    def _record_request(self, pin, integration, span, args, kwargs):
        super()._record_request(pin, integration, span, args, kwargs)

    def _record_response(self, pin, integration, span, args, kwargs, resp, error):
        if not resp:
            return
        if resp.get("results"):
            results = resp["results"][0]
            categories = results.get("categories", {})
            scores = results.get("category_scores", {})
            flagged = results.get("flagged")
            mod_id = resp.get("id", "")
            model = resp.get("model", "")
            for category in categories.keys():
                span.set_metric("openai.response.category_scores.%s" % category, scores.get(category, 0))
                category_flag = categories.get(category)
                if category_flag is not None:
                    span.set_metric("openai.response.categories.%s" % category, int(category_flag))
            if flagged is not None:
                span.set_metric("openai.response.flagged", int(flagged))
            span.set_tag_str("openai.response.id", mod_id)
            span.set_tag_str("openai.response.model", model)
        return resp


class _BaseFileHook(_EndpointHook):
    ENDPOINT_NAME = "files"


class _FileCreateHook(_BaseFileHook):
    _request_arg_params = (
        None,
        "purpose",
        "model",
        "api_key",
        "api_base",
        "api_type",
        "api_version",
        "organization",
        "user_provided_filename",
    )
    HTTP_METHOD_TYPE = "POST"
    OPERATION_ID = "createFile"

    def _record_request(self, pin, integration, span, args, kwargs):
        super()._record_request(pin, integration, span, args, kwargs)
        fp = args[1]
        if fp and hasattr(fp, "name"):
            span.set_tag_str("openai.request.filename", fp.name.split("/")[-1])
        else:
            span.set_tag_str("openai.request.filename", "")

    def _record_response(self, pin, integration, span, args, kwargs, resp, error):
        if not resp:
            return
        span.set_tag_str("openai.response.id", resp.get("id", ""))
        span.set_metric("openai.response.bytes", resp.get("bytes", 0))
        span.set_metric("openai.response.created_at", resp.get("created_at", ""))
        span.set_tag_str("openai.response.filename", resp.get("filename", ""))
        span.set_tag_str("openai.response.purpose", resp.get("purpose", ""))
        span.set_tag_str("openai.response.status", resp.get("status", ""))
        span.set_tag_str("openai.response.status_details", str(resp.get("status_details", "")))
        return resp


class _FileDownloadHook(_BaseFileHook):
    _request_arg_params = (None, "api_key", "api_base", "api_type", "api_version", "organization")
    HTTP_METHOD_TYPE = "GET"
    OPERATION_ID = "downloadFile"
    ENDPOINT_NAME = "files/*/content"

    def _record_request(self, pin, integration, span, args, kwargs):
        super()._record_request(pin, integration, span, args, kwargs)
        span.set_tag_str("openai.request.file_id", args[1])

    def _record_response(self, pin, integration, span, args, kwargs, resp, error):
        if not resp:
            return
        if isinstance(resp, bytes):
            span.set_metric("openai.response.total_bytes", len(resp))
        else:
            span.set_metric("openai.response.total_bytes", getattr(resp, "total_bytes", 0))
        return resp


class _BaseFineTuneHook(_EndpointHook):
    _resp_params = ("id", "model", "fine_tuned_model", "status")

    def _record_response(self, pin, integration, span, args, kwargs, resp, error):
        if not resp:
            return
        for resp_param in self._resp_params:
            if isinstance(resp.get(resp_param), str):
                span.set_tag_str("openai.response.%s" % resp_param, resp.get(resp_param, ""))
            else:
                span.set_tag_str("openai.response.%s" % resp_param, str(resp.get(resp_param, "")))
        span.set_metric("openai.response.created_at", resp.get("created_at", 0))
        span.set_metric("openai.response.updated_at", resp.get("updated_at", 0))
        span.set_metric("openai.response.events_count", len(resp.get("events", [])))
        span.set_metric("openai.response.result_files_count", len(resp.get("result_files", [])))
        span.set_metric("openai.response.training_files_count", len(resp.get("training_files", [])))
        span.set_metric("openai.response.validation_files_count", len(resp.get("validation_files", [])))
        for k, v in resp.get("hyperparams", {}).items():
            if v:
                span.set_metric("openai.response.hyperparams.%s" % k, v)
            else:
                span.set_tag_str("openai.response.hyperparams.%s" % k, str(v))

        return resp


class _FineTuneCreateHook(_BaseFineTuneHook):
    _request_arg_params = ("api_key", "api_base", "api_type", "request_id", "api_version", "organization")
    _request_kwarg_params = (
        "training_file",
        "validation_file",
        "model",
        "n_epochs",
        "batch_size",
        "learning_rate_multiplier",
        "prompt_loss_weight",
        "compute_classification_metrics",
        "classification_n_classes",
        "classification_positive_class",
        "suffix",
    )
    ENDPOINT_NAME = "fine-tunes"
    HTTP_METHOD_TYPE = "POST"
    OPERATION_ID = "createFineTune"

    def _record_request(self, pin, integration, span, args, kwargs):
        super()._record_request(pin, integration, span, args, kwargs)
        if "classification_betas" in kwargs:
            span.set_metric("openai.request.classification_betas_count", len(kwargs.get("classification_betas", [])))
        return


class _FineTuneCancelHook(_BaseFineTuneHook):
    _request_arg_params = (None, "api_key", "api_type", "request_id", "api_version")
    _request_kwarg_params = ("user",)
    ENDPOINT_NAME = "fine-tunes/*/cancel"
    HTTP_METHOD_TYPE = "POST"
    OPERATION_ID = "cancelFineTune"

    def _record_request(self, pin, integration, span, args, kwargs):
        super()._record_request(pin, integration, span, args, kwargs)
        span.set_tag_str("openai.request.fine_tune_id", args[1])
        return


class _FineTuneListEventsHook(_BaseFineTuneHook):
    _request_kwarg_params = ("stream", "user")
    ENDPOINT_NAME = "fine-tunes/*/events"
    HTTP_METHOD_TYPE = "GET"
    OPERATION_ID = "listFineTuneEvents"

    def _record_request(self, pin, integration, span, args, kwargs):
        super()._record_request(pin, integration, span, args, kwargs)
        span.set_tag_str("openai.request.fine_tune_id", args[1])
        return

    def _record_response(self, pin, integration, span, args, kwargs, resp, error):
        if not resp:
            return
        span.set_metric("openai.response.count", len(resp.get("data", [])))
        return resp
