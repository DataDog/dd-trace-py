from typing import Any
from typing import Dict
from typing import Set
from typing import TYPE_CHECKING
from typing import Tuple


if TYPE_CHECKING:
    from ddtrace.span import Span


BLACKLIST_ENDPOINT = ["kms", "sts"]
BLACKLIST_ENDPOINT_TAGS = {
    "s3": ["params.Body"],
}


def _flatten_dict(d, sep=".", prefix=""):
    # type: (Dict[str, Any], str, str) -> Dict[str, Any]
    """
    Returns a normalized dict of depth 1 with keys in order of embedding.
    """
    # adapted from https://stackoverflow.com/a/19647596
    return (
        {prefix + sep + k if prefix else k: v for kk, vv in d.items() for k, v in _flatten_dict(vv, sep, kk).items()}
        if isinstance(d, dict)
        else {prefix: d}
    )


def truncate_arg_value(value, max_len=1024):
    # type: (Any, int) -> Any
    """Truncate values which are bytes and greater than `max_len`.
    Useful for parameters like 'Body' in `put_object` operations.
    """
    if isinstance(value, bytes) and len(value) > max_len:
        return b"..."

    return value


def add_span_arg_tags(
    span,  # type: Span
    endpoint_name,  # type: str
    args,  # type: Tuple[Any]
    args_names,  # type: Tuple[str]
    args_traced,  # type: Set[str]
):
    # type: (...) -> None
    if endpoint_name not in BLACKLIST_ENDPOINT:
        blacklisted = BLACKLIST_ENDPOINT_TAGS.get(endpoint_name, [])
        tags = dict((name, value) for (name, value) in zip(args_names, args) if name in args_traced)
        tags = _flatten_dict(tags)
        tags = {k: truncate_arg_value(v) for k, v in tags.items() if k not in blacklisted}
        span.set_tags(tags)


REGION = "aws.region"
AGENT = "aws.agent"
OPERATION = "aws.operation"
