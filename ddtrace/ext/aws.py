from typing import Any
from typing import Set
from typing import TYPE_CHECKING
from typing import Tuple

from ddtrace.contrib.trace_utils import flatten_dict


if TYPE_CHECKING:
    from ddtrace.span import Span


EXCLUDED_ENDPOINT = {"kms", "sts"}
EXCLUDED_ENDPOINT_TAGS = {
    "s3": {"params.Body"},
    "firehose": {"params.Records"},
}


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
    if endpoint_name not in EXCLUDED_ENDPOINT:
        tags = dict((name, value) for (name, value) in zip(args_names, args) if name in args_traced)
        flat_tags = flatten_dict(tags, exclude=EXCLUDED_ENDPOINT_TAGS.get(endpoint_name))
        span.set_tags({k: truncate_arg_value(v) for k, v in flat_tags.items()})


REGION = "aws.region"
AGENT = "aws.agent"
OPERATION = "aws.operation"
