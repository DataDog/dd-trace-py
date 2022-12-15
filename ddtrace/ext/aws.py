from typing import Any
from typing import Dict
from typing import FrozenSet
from typing import Set
from typing import TYPE_CHECKING
from typing import Tuple

from ddtrace.contrib.trace_utils import set_flattened_tags


if TYPE_CHECKING:  # pragma: no cover
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


def _add_api_param_span_tags(span, endpoint_name, params):
    # type: (Span, str, Dict[str, Any]) -> None
    if endpoint_name == "cloudwatch":
        log_group_name = params.get("logGroupName")
        if log_group_name:
            span.set_tag_str("aws.cloudwatch.logs.log_group_name", log_group_name)
    elif endpoint_name == "dynamodb":
        table_name = params.get("TableName")
        if table_name:
            span.set_tag_str("aws.dynamodb.table_name", table_name)
    elif endpoint_name == "kinesis":
        stream_name = params.get("StreamName")
        if stream_name:
            span.set_tag_str("aws.kinesis.stream_name", stream_name)
    elif endpoint_name == "redshift":
        cluster_identifier = params.get("ClusterIdentifier")
        if cluster_identifier:
            span.set_tag_str("aws.redshift.cluster_identifier", cluster_identifier)
    elif endpoint_name == "s3":
        bucket_name = params.get("Bucket")
        if bucket_name:
            span.set_tag_str("aws.s3.bucket_name", bucket_name)
    elif endpoint_name == "sns":
        topic_arn = params.get("TopicArn")
        if topic_arn:
            span.set_tag_str("aws.sns.topic_arn", topic_arn)
    elif endpoint_name == "sqs":
        queue_name = params.get("QueueName") or params.get("QueueUrl")
        if queue_name:
            span.set_tag_str("aws.sqs.queue_name", queue_name)


REGION = "aws.region"
AGENT = "aws.agent"
OPERATION = "aws.operation"
