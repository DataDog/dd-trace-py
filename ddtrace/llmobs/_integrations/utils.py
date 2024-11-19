from ddtrace.llmobs._utils import _get_attr

def extract_model_name(instance, model_name_attr):
    """Extract the model name from the instance.
    Model names are stored in the format `"models/{model_name}"`
    so we do our best to return the model name instead of the full string.
    """
    model_name = getattr(instance, model_name_attr, "")
    if not model_name or not isinstance(model_name, str):
        return ""
    if "/" in model_name:
        return model_name.split("/")[-1]
    return model_name

def tag_request_content_part(tag_prefix, span, integration, part, part_idx, content_idx):
    """Tag the generation span with request content parts."""
    text = _get_attr(part, "text", "")
    function_call = _get_attr(part, "function_call", None)
    function_response = _get_attr(part, "function_response", None)
    span.set_tag_str(
        "%s.request.contents.%d.parts.%d.text" % (tag_prefix, content_idx, part_idx), integration.trunc(str(text))
    )
    if function_call:
        function_call_dict = type(function_call).to_dict(function_call)
        span.set_tag_str(
            "%s.request.contents.%d.parts.%d.function_call.name" % (tag_prefix, content_idx, part_idx),
            integration.trunc(str(function_call_dict.get("name", ""))),
        )
        span.set_tag_str(
            "%s.request.contents.%d.parts.%d.function_call.args" % (tag_prefix, content_idx, part_idx),
            integration.trunc(str(function_call_dict.get("args", {}))),
        )
    if function_response:
        function_response_dict = type(function_response).to_dict(function_response)
        span.set_tag_str(
            "%s.request.contents.%d.parts.%d.function_response.name" % (tag_prefix, content_idx, part_idx),
            str(function_response_dict.get("name", "")),
        )
        span.set_tag_str(
            "%s.request.contents.%d.parts.%d.function_response.response" % (tag_prefix, content_idx, part_idx),
            integration.trunc(str(function_response_dict.get("response", {}))),
        )

def tag_response_part(tag_prefix, span, integration, part, part_idx, candidate_idx):
    """Tag the generation span with response part text and function calls."""
    text = part.get("text", "")
    span.set_tag_str(
        "%s.response.candidates.%d.content.parts.%d.text" % (tag_prefix, candidate_idx, part_idx),
        integration.trunc(str(text)),
    )
    function_call = part.get("function_call", None)
    if not function_call:
        return
    span.set_tag_str(
        "%s.response.candidates.%d.content.parts.%d.function_call.name" % (tag_prefix, candidate_idx, part_idx),
        integration.trunc(str(function_call.get("name", ""))),
    )
    span.set_tag_str(
        "%s.response.candidates.%d.content.parts.%d.function_call.args" % (tag_prefix, candidate_idx, part_idx),
        integration.trunc(str(function_call.get("args", {}))),
    )