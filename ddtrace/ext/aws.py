from typing import Any
from typing import FrozenSet
from typing import Set
from typing import TYPE_CHECKING
from typing import Tuple

from ddtrace.contrib.trace_utils import set_flattened_tags


if TYPE_CHECKING:
    from ddtrace.span import Span


EXCLUDED_ENDPOINT = frozenset({"kms", "sts", "sns", "kinesis", "events"})
EXCLUDED_ENDPOINT_TAGS = {
    "firehose": frozenset({"params.Records"}),
    "secretsmanager": frozenset({"params.SecretString", "params.SecretBinary"}),
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
        exclude_set = EXCLUDED_ENDPOINT_TAGS.get(endpoint_name, frozenset())  # type: FrozenSet[str]
        set_flattened_tags(
            span,
            items=((name, value) for (name, value) in zip(args_names, args) if name in args_traced),
            exclude_policy=lambda tag: tag in exclude_set or tag.endswith("Body"),
            processor=truncate_arg_value,
        )


REGION = "aws.region"
AGENT = "aws.agent"
OPERATION = "aws.operation"
