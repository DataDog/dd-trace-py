from typing import AsyncGenerator
from typing import Generator

from ..trace_utils import set_flattened_tags
from .utils import _est_tokens
from .utils import _set_openai_api_key_tag


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
        span.set_tag("openai.response.object", self._default_name)
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
        prompt = kwargs.get("prompt", "")
        if integration.is_pc_sampled_span(span):
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
        return

    def _post_response(self, pin, integration, span, args, kwargs, resp, error):
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
                    if integration.is_pc_sampled_span(span):
                        span.set_tag_str("openai.response.choices.%d.text" % idx, integration.trunc(choice.get("text")))
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
        messages = kwargs.get("messages")
        if messages and integration.is_pc_sampled_span(span):
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
        return

    def _post_response(self, pin, integration, span, args, kwargs, resp, error):
        if resp and not kwargs.get("stream"):
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
    _request_tag_attrs = [
        "user",
    ]
    _default_name = "embeddings"

    def _pre_response(self, pin, integration, span, args, kwargs):
        """
        Embedding endpoint allows multiple inputs, each of which we specify a request tag for, so have to
        manually set them in _pre_response().
        """
        text_input = kwargs.get("input")
        if isinstance(text_input, list):
            for idx, inp in enumerate(text_input):
                span.set_tag_str("openai.request.input.%d" % idx, integration.trunc(inp))
        else:
            span.set_tag("openai.request.input", text_input)
        return

    def _post_response(self, pin, integration, span, args, kwargs, resp, error):
        if not resp:
            return
        if "data" in resp:
            span.set_tag("openai.response.data.num-embeddings", len(resp["data"]))
            for result in resp["data"]:
                idx = result["index"]
                span.set_tag("openai.response.data.%d.embedding-length" % idx, len(result["embedding"]))
        integration.record_usage(span, resp.get("usage"))
        return resp


class _ListHook(_EndpointHook):
    """
    Hook for openai.ListableAPIResource, which is used by Model.list, Files.list, and FineTunes.list.
    """

    _default_name = "list"

    def _pre_response(self, pin, integration, span, args, kwargs):
        _set_openai_api_key_tag(span, args, kwargs, api_key_index=1)
        return

    def _post_response(self, pin, integration, span, args, kwargs, resp, error):
        span.set_tag_str("openai.response.object", self._default_name)
        span.set_tag("openai.response.data.num", len(resp.get("data", [])))
        for idx, data_obj in enumerate(resp.get("data", [])):
            for k, v in data_obj.items():
                if isinstance(v, dict):
                    set_flattened_tags(
                        span,
                        [
                            ("openai.response.data.{}.{}.{}".format(idx, k, dict_k), dict_v)
                            for dict_k, dict_v in v.items()
                        ],
                    )
                elif isinstance(v, list):
                    for list_idx, list_obj in enumerate(v):
                        set_flattened_tags(
                            span,
                            [
                                ("openai.response.data.{}.{}.{}.{}".format(idx, k, list_idx, dict_k), dict_v)
                                for dict_k, dict_v in list_obj.items()
                            ],
                        )
                    # TODO: WHAT DO WE DO HERE WHEN OBJ IS A LIST???
                else:
                    span.set_tag("openai.response.data.%d.%s" % (idx, k), v)

        # TODO: what info to return from the response? Do we need to return all listed info?
        return resp


class _RetrieveHook(_EndpointHook):
    """Hook for openai.APIResource, which is used by Model.retrieve, File.retrieve, and FineTunes.retrieve."""

    _default_name = "retrieve"

    def _pre_response(self, pin, integration, span, args, kwargs):
        _set_openai_api_key_tag(span, args, kwargs, api_key_index=2)
        span.set_tag_str("openai.request.id", args[1])
        if args[0].OBJECT_NAME == "models":
            span.set_tag_str("openai.model", args[1])
        return

    def _post_response(self, pin, integration, span, args, kwargs, resp, error):
        if not resp:
            return
        span.set_tag_str("openai.response.object", self._default_name)
        if hasattr(resp, "created"):
            span.set_tag("openai.response.created", resp["created"])
        if hasattr(resp, "created_at"):
            span.set_tag("openai.response.created_at", resp["created_at"])
        return resp


class _EditHook(_EndpointHook):
    _request_tag_attrs = [
        "n",
        "temperature",
        "top_p",
    ]
    _default_name = "edits"

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
        span.set_tag("openai.response.choices.num", len(choices))
        if integration.is_pc_sampled_span(span):
            for choice in choices:
                idx = choice["index"]
                span.set_tag_str("openai.response.choices.%d.text" % idx, integration.trunc(choice.get("text")))
        integration.record_usage(span, resp.get("usage"))
        if integration.is_pc_sampled_log(span):
            instruction = kwargs.get("instrution", "")
            input_text = kwargs.get("input", "")
            integration.log(
                span,
                "info" if error is None else "error",
                "sampled %s" % self._default_name,
                attrs={
                    "instruction": instruction,
                    "input": input_text,
                    "choices": resp["choices"] if resp and "choices" in resp else [],
                },
            )
        return resp


class _ImageHook(_EndpointHook):
    _default_name = "images"

    def _post_response(self, pin, integration, span, args, kwargs, resp, error):
        if not resp:
            return
        span.set_tag_str("openai.response.object", self._default_name)
        if integration.is_pc_sampled_span(span):
            choices = resp.get("data", [])
            span.set_tag("openai.response.created", resp.get("created"))
            span.set_tag("openai.response.choices.num", len(choices))
            if integration.is_pc_sampled_span(span):
                for idx, choice in enumerate(choices):
                    if choice.get("b64_json"):
                        span.set_tag_str(
                            "openai.response.choices.%d.b64_json" % idx, integration.trunc(choice.get("b64_json"))
                        )
                    else:
                        span.set_tag_str("openai.response.choices.%d.url" % idx, integration.trunc(choice.get("url")))
        if integration.is_pc_sampled_log(span):
            attrs_dict = {
                "choices": resp["data"] if resp and "data" in resp else [],
            }
            if "prompt" in self._request_tag_attrs:
                attrs_dict.update({"prompt": kwargs.get("prompt", "")})
            if "image" in self._request_tag_attrs:
                image = args[1] or kwargs.get("image", "")
                attrs_dict.update({"image": getattr(image, "name", "")})
            if "mask" in self._request_tag_attrs:
                mask = args[2] or kwargs.get("mask", "")
                attrs_dict.update({"mask": getattr(mask, "name", "")})
            integration.log(
                span, "info" if error is None else "error", "sampled %s" % self._default_name, attrs=attrs_dict
            )
        return resp


class _ImageCreateHook(_ImageHook):
    _request_tag_attrs = ["prompt", "n", "size", "response_format", "user"]
    _default_name = "images.creation"

    def _pre_response(self, pin, integration, span, args, kwargs):
        _set_openai_api_key_tag(span, args, kwargs, api_key_index=1)
        return


class _ImageEditHook(_ImageHook):
    _request_tag_attrs = ["image", "mask", "prompt", "n", "size", "response_format", "user"]
    _default_name = "images.edits"

    def _pre_response(self, pin, integration, span, args, kwargs):
        _set_openai_api_key_tag(span, args, kwargs, api_key_index=3)
        if integration.is_pc_sampled_span(span):
            image = args[1] or kwargs.get("image", "")
            mask = args[2] or kwargs.get("mask", "")
            span.set_tag_str("openai.request.image", integration.trunc(getattr(image, "name", "")))
            span.set_tag_str("openai.request.mask", integration.trunc(getattr(mask, "name", "")))
        return


class _ImageVariationHook(_ImageHook):
    _request_tag_attrs = ["image", "n", "size", "response_format", "user"]
    _default_name = "images.create_variations"

    def _pre_response(self, pin, integration, span, args, kwargs):
        _set_openai_api_key_tag(span, args, kwargs, api_key_index=2)
        if integration.is_pc_sampled_span(span):
            image = args[1] or kwargs.get("image", "")
            span.set_tag_str("openai.request.image", integration.trunc(getattr(image, "name", "")))
        return


class _AudioHook(_EndpointHook):
    _default_name = "audio"

    def _pre_response(self, pin, integration, span, args, kwargs):
        _set_openai_api_key_tag(span, args, kwargs, api_key_index=3)
        if integration.is_pc_sampled_span(span):
            model = args[1] or kwargs.get("model", "")
            file_input = args[2] or kwargs.get("file", "")
            span.set_tag_str("openai.request.file", integration.trunc(getattr(file_input, "name", "")))
            span.set_tag_str("openai.model", model)
        return

    def _post_response(self, pin, integration, span, args, kwargs, resp, error):
        if not resp:
            return
        span.set_tag_str("openai.response.object", self._default_name)
        if integration.is_pc_sampled_span(span):
            if isinstance(resp, str):
                text = resp
            elif isinstance(resp, dict):
                text = resp.get("text", "")
            else:
                text = ""
            span.set_tag_str("openai.response.text", integration.trunc(text))
        if integration.is_pc_sampled_log(span):
            file_input = args[2] or kwargs.get("file", "")
            integration.log(
                span,
                "info" if error is None else "error",
                "sampled %s" % self._default_name,
                attrs={
                    "file": getattr(file_input, "name", ""),
                    "text": resp["text"] if isinstance(resp, dict) else resp,
                },
            )
        return resp


class _AudioTranscriptionHook(_AudioHook):
    _request_tag_attrs = [
        "prompt",
        "response_format",
        "temperature",
        "language",
    ]
    _default_name = "audio.transcriptions"


class _AudioTranslationHook(_AudioHook):
    _request_tag_attrs = [
        "prompt",
        "response_format",
        "temperature",
    ]
    _default_name = "audio.translations"


class _ModerationHook(_EndpointHook):
    _default_name = "moderations"

    def _pre_response(self, pin, integration, span, args, kwargs):
        _set_openai_api_key_tag(span, args, kwargs, api_key_index=3)
        input_text = args[1]
        model = args[2]
        span.set_tag_str("openai.request.input", integration.trunc(input_text))
        span.set_tag_str("openai.model", model)
        return

    def _post_response(self, pin, integration, span, args, kwargs, resp, error):
        if not resp:
            return
        if resp.get("results"):
            results = resp["results"][0]
            categories = results.get("categories", {})
            scores = results.get("category_scores", {})
            flagged = results.get("flagged", "")
            for category in categories.keys():
                span.set_metric("openai.response.category_scores.%s" % category, scores.get(category, 0))
                span.set_tag_str("openai.response.categories.%s" % category, str(categories.get(category, "")))
            span.set_tag_str("openai.response.flagged", str(flagged))

        return resp


class _FileCreateHook(_EndpointHook):
    _default_name = "files.create"

    def _pre_response(self, pin, integration, span, args, kwargs):
        _set_openai_api_key_tag(span, args, kwargs, api_key_index=4)
        file_input = args[1] or ""
        purpose = args[2] or ""
        model = args[3] or ""
        user_provided_filename = args[9] or ""
        span.set_tag_str("openai.request.file", integration.trunc(getattr(file_input, "name", "")))
        span.set_tag_str("openai.request.purpose", purpose)
        span.set_tag_str("openai.request.user_provided_filename", integration.trunc(user_provided_filename))
        if model:
            span.set_tag_str("openai.model", model)
        return

    def _post_response(self, pin, integration, span, args, kwargs, resp, error):
        if not resp:
            return
        for k, v in resp.items():
            span.set_tag("openai.response.%s" % k, v if v else "")
        span.set_tag_str("openai.response.object", self._default_name)

        return resp


class _FileDeleteHook(_EndpointHook):
    # TODO: This will probably conflict with fine-tune delete model
    _default_name = "files.delete"

    def _pre_response(self, pin, integration, span, args, kwargs):
        _set_openai_api_key_tag(span, args, kwargs, api_key_index=None)
        span.set_tag_str("openai.request.file_id", args[1] or kwargs.get(""))
        pass

    def _post_response(self, pin, integration, span, args, kwargs, resp, error):
        if not resp:
            return
        for k, v in resp.data.items():
            span.set_tag("openai.response.%s" % k, v)
        span.set_tag_str("openai.response.object", self._default_name)
        return resp


class _FileDownloadHook(_EndpointHook):
    _default_name = "files.download"

    def _pre_response(self, pin, integration, span, args, kwargs):
        _set_openai_api_key_tag(span, args, kwargs, api_key_index=2)
        span.set_tag_str("openai.request.id", args[1] or kwargs.get(""))
        pass

    def _post_response(self, pin, integration, span, args, kwargs, resp, error):
        return resp


class _FineTuneCreateHook(_EndpointHook):
    _request_tag_attrs = [
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
    _default_name = "fine-tunes.create"

    def _pre_response(self, pin, integration, span, args, kwargs):
        _set_openai_api_key_tag(span, args, kwargs, api_key_index=1)
        train_file_id = kwargs.get("training_file", "")
        valid_file_id = kwargs.get("validation_file", "")
        span.set_tag_str("openai.request.training_file", train_file_id)
        span.set_tag_str("openai.request.validation_file", valid_file_id)

    def _post_response(self, pin, integration, span, args, kwargs, resp, error):
        if not resp:
            return
        for idx, event in enumerate(resp.get("events", [])):
            for k, v in event.items():
                span.set_tag("openai.response.events.%d.%s" % (idx, k), v)
        span.set_tag_str("openai.response.fine_tuned_model", resp.get("fine_tuned_model", ""))
        for k, v in resp.get("hyperparams", {}).items():
            span.set_metric("openai.response.hyperparams.%s" % k, v)
        span.set_tag_str("openai.response.organization_id", resp.get("organization_id", ""))
        span.set_tag_str("openai.response.status", resp.get("status", ""))
        for idx, train_file in enumerate(resp.get("training_files", [])):
            for k, v in train_file.items():
                span.set_tag("openai.response.training_files.%d.%s" % (idx, k), v)
        for idx, valid_file in enumerate(resp.get("validation_files", [])):
            for k, v in valid_file.items():
                span.set_tag("openai.response.validation_files.%d.%s" % (idx, k), v)
        span.set_tag("openai.response.created_at", resp.get("created_at", ""))
        span.set_tag("openai.response.updated_at", resp.get("updated_at", ""))

        return resp
