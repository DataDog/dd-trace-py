from typing import Callable
from ddtrace.propagation.http import HTTPPropagator

redactable_keys = ["authorization", "x-authorization", "password", "token"]
max_depth = 10

def extract_DD_context_from_messages(messages, extract_from_message: Callable):
    ctx = None
    if len(messages) >= 1:
        message = messages[0]
        context_json = extract_from_message(message)
        if context_json is not None:
            child_of = HTTPPropagator.extract(context_json)
            if child_of.trace_id is not None:
                ctx = child_of
    return ctx

def tag_object(span, key, obj, depth=0):
    if obj is None:
        return span.set_tag(key, obj)
    if depth >= max_depth:
        return span.set_tag(key, _redact_val(key, str(obj)[0:5000]))
    depth += 1
    if _should_try_string(obj):
        parsed = None
        try:
            parsed = json.loads(obj)
            return tag_object(span, key, parsed, depth)
        except ValueError:
            redacted = _redact_val(key, obj[0:5000])
            return span.set_tag(key, redacted)
    if isinstance(obj, int) or isinstance(obj, float) or isinstance(obj, Decimal):
        return span.set_tag(key, str(obj))
    if isinstance(obj, list):
        for k, v in enumerate(obj):
            formatted_key = f"{key}.{k}"
            tag_object(span, formatted_key, v, depth)
        return
    if hasattr(obj, "items"):
        for k, v in obj.items():
            formatted_key = f"{key}.{k}"
            tag_object(span, formatted_key, v, depth)
        return
    if hasattr(obj, "to_dict"):
        for k, v in obj.to_dict().items():
            formatted_key = f"{key}.{k}"
            tag_object(span, formatted_key, v, depth)
        return
    try:
        value_as_str = str(obj)
    except Exception:
        value_as_str = "UNKNOWN"
    return span.set_tag(key, value_as_str)

def expand_payload_as_tags(span: Span, result: Dict[str, Any], key):
    # TODO add configuration if this is enabled or not DD_TRACE_CLOUD_REQUEST_PAYLOAD_TAGGING
    # TODO add configuration if this is enabled or not DD_TRACE_CLOUD_RESPONSE_PAYLOAD_TAGGING
    #   supported values "all" OR a comma-separated list of JSONPath queries defining payload paths that will be replaced with "redacted"
    # TODO add max depth configuration DD_TRACE_CLOUD_PAYLOAD_TAGGING_MAX_DEPTH (default 10)

    if not result:
        return
    
    # handle response messages list
    if result.get("Messages"):
        message = result["Messages"]
        tag_object(span, key, message) 
        return
    
    # handle params request list
    for key2, value in result.items():
        tag_object(span, key, value)

def payload_expansion(span: Span, message, key, depth=0):
    if message is None:
        return
    if depth >= max_depth:
        return
    else:
        depth += 1
    

def _should_try_string(obj):
    try:
        if isinstance(obj, str) or isinstance(obj, unicode):
            return True
    except NameError:
        if isinstance(obj, bytes):
            return True

    return False


def _redact_val(k, v):
    split_key = k.split(".").pop() or k
    if split_key in redactable_keys:
        return "redacted"
    return v
