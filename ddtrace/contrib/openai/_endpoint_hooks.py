from typing import AsyncGenerator
from typing import Generator

from ..trace_utils import set_flattened_tags
from .utils import _est_tokens
from .utils import _format_bool
from .utils import _format_openai_api_key


class _EndpointHook:
    # Assume base arg signature follows openai.EngineAPIResource.create(...)
    _request_arg_params = ["cls", "api_key", "api_base", "api_type", "request_id", "api_version", "organization"]
    _request_kwarg_params = []
    _prompt_completion = False
    ENDPOINT_NAME = "openai"
    REQUEST_TYPE = ""
    OPERATION_ID = ""

    def _record_request(self, span, integration, args, kwargs):
        """Set base-level openai tags, as well as request params from args and kwargs."""
        endpoint = self.ENDPOINT_NAME
        if endpoint is None:
            endpoint = "/%s" % args[0].OBJECT_NAME
        span.set_tag_str("openai.request.endpoint", endpoint)
        span.set_tag_str("openai.request.method", self.REQUEST_TYPE)

        base_level_tag_args = ("api_base", "api_type", "api_version")
        for idx, arg in enumerate(self._request_arg_params[1:], 1):
            if idx >= len(args):
                break
            if args[idx] is None:
                continue
            if arg in base_level_tag_args:
                span.set_tag("openai.%s" % arg, args[idx] or "")
            elif arg == "organization":
                span.set_tag_str("openai.organization.id", args[idx])
            elif arg == "api_key":
                span.set_tag_str("openai.user.api_key", _format_openai_api_key(args[idx]))
            elif (
                self._prompt_completion is True and integration.is_pc_sampled_span
            ) or self._prompt_completion is False:
                if hasattr(args[idx], "name"):  # For file pointer args
                    span.set_tag_str("openai.request.%s" % arg, integration.trunc(args[idx].name))
                elif isinstance(args[idx], bytes):  # For binary file data
                    span.set_tag_str("openai.request.%s" % arg, "")
                else:
                    span.set_tag("openai.request.%s" % arg, integration.trunc(args[idx]))
        for kw_attr in self._request_kwarg_params:
            if kw_attr in kwargs:
                if isinstance(kwargs[kw_attr], dict):
                    set_flattened_tags(
                        span, [("openai.request.{}.{}".format(kw_attr, k), v) for k, v in kwargs[kw_attr].items()]
                    )
                else:
                    if isinstance(kwargs[kw_attr], str):
                        span.set_tag_str("openai.request.%s" % kw_attr, integration.trunc(kwargs[kw_attr]))
                    else:
                        span.set_tag("openai.request.%s" % kw_attr, kwargs[kw_attr])

    def handle_request(self, pin, integration, span, args, kwargs):
        self._record_request(span, integration, args, kwargs)
        self._pre_response(pin, integration, span, args, kwargs)
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

    _prompt_completion = True

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
    _request_kwarg_params = [
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
    ]
    ENDPOINT_NAME = "/completions"
    REQUEST_TYPE = "POST"
    OPERATION_ID = "createCompletion"

    def _pre_response(self, pin, integration, span, args, kwargs):
        prompt = kwargs.get("prompt", "")
        if integration.is_pc_sampled_span(span):
            if isinstance(prompt, str):
                span.set_tag_str("openai.request.prompt", integration.trunc(prompt))
            elif prompt:
                for idx, p in enumerate(prompt):
                    span.set_tag_str("openai.request.prompt.%d" % idx, integration.trunc(str(p)))
        if kwargs.get("stream"):
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
            span.set_tag("openai.response.choices_count", len(choices))
            span.set_tag("openai.response.created", resp.get("created"))
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
            prompt = kwargs.get("prompt", "")
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
    _request_kwarg_params = [
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
    ]
    ENDPOINT_NAME = "/chat/completions"
    REQUEST_TYPE = "POST"
    OPERATION_ID = "createChatCompletion"

    def _pre_response(self, pin, integration, span, args, kwargs):
        messages = kwargs.get("messages", [])
        if integration.is_pc_sampled_span(span):
            for idx, m in enumerate(messages):
                content = integration.trunc(m.get("content", ""))
                role = integration.trunc(m.get("role", ""))
                name = integration.trunc(m.get("name", ""))
                span.set_tag_str("openai.request.messages.%d.content" % idx, content)
                span.set_tag_str("openai.request.messages.%d.role" % idx, role)
                span.set_tag_str("openai.request.messages.%d.name" % idx, name)
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
        span.set_tag("openai.response.choices_count", len(choices))
        span.set_tag("openai.response.created", resp.get("created"))
        span.set_tag_str("openai.response.id", resp.get("id", ""))
        span.set_tag_str("openai.response.model", resp.get("model", ""))
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
                span.set_tag(
                    "openai.response.choices.%d.message.name" % idx,
                    integration.trunc(choice.get("message").get("name")),
                )
        integration.record_usage(span, resp.get("usage"))
        if integration.is_pc_sampled_log(span):
            messages = kwargs.get("messages")
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
    _request_kwarg_params = ["model", "user"]
    _prompt_completion = False
    ENDPOINT_NAME = "/embeddings"
    REQUEST_TYPE = "POST"
    OPERATION_ID = "createEmbedding"

    def _pre_response(self, pin, integration, span, args, kwargs):
        """
        Embedding endpoint allows multiple inputs, each of which we specify a request tag for, so have to
        manually set them in _pre_response().
        """
        text_input = kwargs.get("input")
        if isinstance(text_input, list) and not isinstance(text_input[0], int):
            for idx, inp in enumerate(text_input):
                span.set_tag_str("openai.request.input.%d" % idx, integration.trunc(str(inp)))
        else:
            span.set_tag("openai.request.input", text_input)
        return

    def _post_response(self, pin, integration, span, args, kwargs, resp, error):
        if not resp:
            return
        span.set_tag_str("openai.response.model", resp.get("model", ""))
        if "data" in resp:
            span.set_tag("openai.response.embeddings_count", len(resp["data"]))
            for result in resp["data"]:
                idx = result["index"]
                span.set_tag("openai.response.embeddings.%d.embedding-length" % idx, len(result["embedding"]))
        integration.record_usage(span, resp.get("usage"))
        return resp


class _ListHook(_EndpointHook):
    """
    Hook for openai.ListableAPIResource, which is used by Model.list, File.list, and FineTune.list.
    """

    _request_arg_params = ["cls", "api_key", "request_id", "api_version", "organization", "api_base", "api_type"]
    _prompt_completion = False
    ENDPOINT_NAME = None
    REQUEST_TYPE = "GET"
    OPERATION_ID = "list"

    def _pre_response(self, pin, integration, span, args, kwargs):
        if span.get_tag("openai.request.endpoint") == "/models":
            span.resource = "listModels"
        elif span.get_tag("openai.request.endpoint") == "/files":
            span.resource = "listFiles"
        elif span.get_tag("openai.request.endpoint") == "/fine-tunes":
            span.resource = "listFineTunes"
        return

    def _post_response(self, pin, integration, span, args, kwargs, resp, error):
        if not resp:
            return
        span.set_tag("openai.response.count", len(resp.get("data", [])))
        return resp


class _RetrieveHook(_EndpointHook):
    """Hook for openai.APIResource, which is used by Model.retrieve, File.retrieve, and FineTune.retrieve."""

    _request_arg_params = ["cls", "id", "api_key", "request_id", "request_timeout"]
    _prompt_completion = False
    ENDPOINT_NAME = None
    REQUEST_TYPE = "GET"
    OPERATION_ID = "retrieve"

    def _pre_response(self, pin, integration, span, args, kwargs):
        if span.get_tag("openai.request.endpoint") == "/models":
            span.resource = "retrieveModel"
        elif span.get_tag("openai.request.endpoint") == "/files":
            span.resource = "retrieveFile"
        elif span.get_tag("openai.request.endpoint") == "/fine-tunes":
            span.resource = "retrieveFineTune"
        return

    def _post_response(self, pin, integration, span, args, kwargs, resp, error):
        if not resp:
            return
        response_attrs = (
            "id",
            "owned_by",
            "parent",
            "root",
            "bytes",
            "created_at",
            "purpose",
            "filename",
            "fine_tuned_model",
            "status",
            "status_details",
            "updated_at ",
        )
        for resp_attr in response_attrs:
            if resp_attr in resp:
                span.set_tag("openai.response.%s" % resp_attr, resp.get(resp_attr, ""))
        if resp.get("permission"):
            for k, v in resp.get("permission", [])[0].items():
                if k != "object":
                    span.set_tag("openai.response.permission.%s" % k, _format_bool(v))
        if resp.get("hyperparams"):
            set_flattened_tags(
                span, [("openai.response.hyperparams.%s" % k, v) for k, v in resp.get("hyperparams", {}).items()]
            )
        for resp_attr in ("result_files", "training_files", "validation_files"):
            if resp_attr in resp:
                span.set_tag("openai.response.%s_count" % resp_attr, len(resp.get(resp_attr, [])))
        return resp


class _DeleteHook(_EndpointHook):
    """Hook for openai.DeletableAPIResource, which is used by File.delete, and Model.delete."""

    _request_arg_params = ["cls", "sid", "api_type", "api_version"]
    _prompt_completion = False
    ENDPOINT_NAME = None
    REQUEST_TYPE = "DELETE"
    OPERATION_ID = "delete"

    def _pre_response(self, pin, integration, span, args, kwargs):
        if span.get_tag("openai.request.endpoint") == "/models":
            span.resource = "deleteModel"
        elif span.get_tag("openai.request.endpoint") == "/files":
            span.resource = "deleteFile"
        return

    def _post_response(self, pin, integration, span, args, kwargs, resp, error):
        if not resp:
            return
        span.set_tag_str("openai.response.id", resp.get("id", ""))
        span.set_tag_str("openai.response.deleted", resp.get("deleted", ""))
        return resp


class _EditHook(_EndpointHook):
    _request_kwarg_params = [
        "model",
        "n",
        "temperature",
        "top_p",
    ]
    _prompt_completion = True
    ENDPOINT_NAME = "/edits"
    REQUEST_TYPE = "POST"
    OPERATION_ID = "createEdit"

    def _pre_response(self, pin, integration, span, args, kwargs):
        if integration.is_pc_sampled_span(span):
            instruction = kwargs.get("instruction", "")
            input_text = kwargs.get("input", "")
            span.set_tag_str("openai.request.instruction", integration.trunc(instruction))
            span.set_tag_str("openai.request.input", integration.trunc(input_text))
        return

    def _post_response(self, pin, integration, span, args, kwargs, resp, error):
        if not resp:
            return
        choices = resp.get("choices", [])
        span.set_tag("openai.response.choices_count", len(choices))
        span.set_tag("openai.response.created", resp.get("created"))
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
    _prompt_completion = True
    ENDPOINT_NAME = "/images"
    REQUEST_TYPE = "POST"

    def _pre_response(self, pin, integration, span, args, kwargs):
        return

    def _post_response(self, pin, integration, span, args, kwargs, resp, error):
        if not resp:
            return
        if integration.is_pc_sampled_span(span):
            choices = resp.get("data", [])
            span.set_tag("openai.response.created", resp.get("created"))
            span.set_tag("openai.response.images_count", len(choices))
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
                attrs_dict.update({"image": getattr(image, "name", "")})
            if "mask" in self._request_kwarg_params:
                mask = args[2] or ""
                attrs_dict.update({"mask": getattr(mask, "name", "")})
            attrs_dict.update({"choices": resp.get("data", [])})
            integration.log(
                span, "info" if error is None else "error", "sampled %s" % self.OPERATION_ID, attrs=attrs_dict
            )
        return resp


class _ImageCreateHook(_ImageHook):
    _request_arg_params = ["cls", "api_key", "api_base", "api_type", "api_version", "organization"]
    _request_kwarg_params = ["prompt", "n", "size", "response_format", "user"]
    ENDPOINT_NAME = "/images/generations"
    OPERATION_ID = "createImage"


class _ImageEditHook(_ImageHook):
    _request_arg_params = ["cls", "image", "mask", "api_key", "api_base", "api_type", "api_version", "organization"]
    _request_kwarg_params = ["prompt", "n", "size", "response_format", "user"]
    ENDPOINT_NAME = "/images/edits"
    OPERATION_ID = "createImageEdit"


class _ImageVariationHook(_ImageHook):
    _request_arg_params = ["cls", "image", "api_key", "api_base", "api_type", "api_version", "organization"]
    _request_kwarg_params = ["n", "size", "response_format", "user"]
    ENDPOINT_NAME = "/images/variations"
    OPERATION_ID = "createImageVariation"


class _BaseAudioHook(_EndpointHook):
    _request_arg_params = ["cls", "model", "file", "api_key", "api_base", "api_type", "api_version", "organization"]
    _prompt_completion = True
    ENDPOINT_NAME = "/audio"
    REQUEST_TYPE = "POST"

    def _pre_response(self, pin, integration, span, args, kwargs):
        return

    def _post_response(self, pin, integration, span, args, kwargs, resp, error):
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
                    span.set_tag("openai.response.duration", resp.get("duration"))
                if resp.get("segments", []):
                    span.set_tag("openai.response.segments_count", len(resp["segments"]))
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
                    "file": getattr(file_input, "name", ""),
                    "text": resp["text"] if isinstance(resp, dict) else resp,
                },
            )
        return resp


class _AudioTranscriptionHook(_BaseAudioHook):
    _request_kwarg_params = [
        "prompt",
        "response_format",
        "temperature",
        "language",
    ]
    ENDPOINT_NAME = "/audio/transcriptions"
    OPERATION_ID = "createTranscription"


class _AudioTranslationHook(_BaseAudioHook):
    _request_kwarg_params = [
        "prompt",
        "response_format",
        "temperature",
    ]
    ENDPOINT_NAME = "/audio/translations"
    OPERATION_ID = "createTranslation"


class _ModerationHook(_EndpointHook):
    _request_arg_params = ["cls", "input", "model", "api_key"]
    _prompt_completion = False
    ENDPOINT_NAME = "/moderations"
    REQUEST_TYPE = "POST"
    OPERATION_ID = "createModeration"

    def _pre_response(self, pin, integration, span, args, kwargs):
        return

    def _post_response(self, pin, integration, span, args, kwargs, resp, error):
        if not resp:
            return
        if resp.get("results"):
            results = resp["results"][0]
            categories = results.get("categories", {})
            scores = results.get("category_scores", {})
            flagged = results.get("flagged", "")
            mod_id = resp.get("id", "")
            model = resp.get("model", "")
            for category in categories.keys():
                span.set_metric("openai.response.category_scores.%s" % category, scores.get(category, 0))
                span.set_tag("openai.response.categories.%s" % category, _format_bool(categories.get(category, "")))
            span.set_tag("openai.response.flagged", _format_bool(flagged))
            span.set_tag_str("openai.response.id", mod_id)
            span.set_tag_str("openai.response.model", model)
        return resp


class _BaseFileHook(_EndpointHook):
    _prompt_completion = False
    ENDPOINT_NAME = "/files"

    def _pre_response(self, pin, integration, span, args, kwargs):
        return


class _FileCreateHook(_BaseFileHook):
    _request_arg_params = [
        "cls",
        "file",
        "purpose",
        "model",
        "api_key",
        "api_base",
        "api_type",
        "api_version",
        "organization",
        "user_provided_filename",
    ]
    REQUEST_TYPE = "POST"
    OPERATION_ID = "createFile"

    def _post_response(self, pin, integration, span, args, kwargs, resp, error):
        if not resp:
            return
        span.set_tag_str("openai.response.id", resp.get("id", ""))
        span.set_tag("openai.response.bytes", resp.get("bytes", ""))
        span.set_tag("openai.response.created_at", resp.get("created_at", ""))
        span.set_tag_str("openai.response.filename", integration.trunc(resp.get("filename", "")))
        span.set_tag_str("openai.response.purpose", resp.get("purpose", ""))
        span.set_tag_str("openai.response.status", resp.get("status", ""))
        span.set_tag("openai.response.status_details", resp.get("status_details", ""))
        return resp


class _FileDownloadHook(_BaseFileHook):
    _request_arg_params = ["cls", "id", "api_key", "api_base", "api_type", "api_version", "organization"]
    REQUEST_TYPE = "GET"
    OPERATION_ID = "downloadFile"

    def _post_response(self, pin, integration, span, args, kwargs, resp, error):
        if not resp:
            return
        span.set_tag("openai.response.total_bytes", getattr(resp, "total_bytes", 0))
        return resp


class _BaseFineTuneHook(_EndpointHook):
    _prompt_completion = False

    def _pre_response(self, pin, integration, span, args, kwargs):
        return

    def _post_response(self, pin, integration, span, args, kwargs, resp, error):
        if not resp:
            return

        for k, v in resp.items():
            if isinstance(v, dict):
                set_flattened_tags(
                    span, [("openai.response.%s.%s" % (k, dict_k), dict_v) for dict_k, dict_v in v.items()]
                )
            elif isinstance(v, list):
                for idx, list_obj in enumerate(v):
                    if isinstance(list_obj, dict):
                        set_flattened_tags(
                            span,
                            [
                                ("openai.response.%s.%d.%s" % (k, idx, dict_k), dict_v)
                                for dict_k, dict_v in list_obj.items()
                            ],
                        )
                    else:
                        span.set_tag("openai.response.%s.%d" % (k, idx), list_obj)
            else:
                span.set_tag("openai.response.%s" % k, integration.trunc(str(v)))
        return resp


class _FineTuneCreateHook(_BaseFineTuneHook):
    _request_kwarg_params = [
        "training_file",
        "validation_file",
        "n_epochs",
        "batch_size",
        "learning_rate_multiplier",
        "prompt_loss_weight",
        "compute_classification_metrics",
        "classification_n_classes",
        "classification_positive_class",
        "classification_betas",
        "suffix",
    ]
    ENDPOINT_NAME = "/fine-tunes"
    REQUEST_TYPE = "POST"
    OPERATION_ID = "createFineTune"


class _FineTuneCancelHook(_BaseFineTuneHook):
    _request_arg_params = ["cls", "id", "api_key", "api_type", "request_id", "api_version"]
    ENDPOINT_NAME = "/fine-tunes/cancel"
    REQUEST_TYPE = "POST"
    OPERATION_ID = "cancelFineTune"


class _FineTuneListEventsHook(_BaseFineTuneHook):
    _request_arg_params = ["cls", "id"]
    ENDPOINT_NAME = "/fine-tunes/events"
    REQUEST_TYPE = "GET"
    OPERATION_ID = "listFineTuneEvents"

    def _post_response(self, pin, integration, span, args, kwargs, resp, error):
        if not resp:
            return
        span.set_tag("openai.response.count", len(resp.get("data", [])))
        return resp
