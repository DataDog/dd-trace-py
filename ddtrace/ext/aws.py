import re
from typing import TYPE_CHECKING  # noqa:F401
from typing import Any  # noqa:F401


if TYPE_CHECKING:  # pragma: no cover
    from ddtrace.trace import Span  # noqa:F401


# A DynamoDB TableName given as a full table ARN: arn:<partition>:dynamodb:<region>:<account>:table/<name>
_DYNAMODB_TABLE_ARN_RE = re.compile(r"arn:[^:]+:dynamodb:[^:]+:[0-9]{12}:table/[^/]+")


def truncate_arg_value(value: Any, max_len: int = 1024) -> Any:
    """Truncate values which are bytes and greater than `max_len`.
    Useful for parameters like 'Body' in `put_object` operations.
    """
    if isinstance(value, bytes) and len(value) > max_len:
        return b"..."

    return value


def _add_api_param_span_tags(span: "Span", endpoint_name: str, params: dict[str, Any]) -> None:
    # Note: Only some boto3 requests will supply these params
    # i.e. that might explain why you see these tags being set to empty strings
    if endpoint_name == "cloudwatch":
        log_group_name = params.get("logGroupName")
        if log_group_name:
            span._set_attribute("aws.cloudwatch.logs.log_group_name", log_group_name)
            span._set_attribute("loggroupname", log_group_name)
    elif endpoint_name == "dynamodb":
        table_name = params.get("TableName")
        if table_name:
            span._set_attribute("aws.dynamodb.table_name", table_name)
            span._set_attribute("tablename", table_name)
            if isinstance(table_name, str) and _DYNAMODB_TABLE_ARN_RE.fullmatch(table_name):
                span._set_attribute(TABLE_ARN, table_name)
    elif endpoint_name == "kinesis":
        stream_name = params.get("StreamName")
        if stream_name:
            span._set_attribute("aws.kinesis.stream_name", stream_name)
            span._set_attribute("streamname", stream_name)
        stream_arn = params.get("StreamARN")
        if stream_arn:
            span._set_attribute(STREAM_ARN, stream_arn)
    elif endpoint_name == "redshift":
        cluster_identifier = params.get("ClusterIdentifier")
        if cluster_identifier:
            span._set_attribute("aws.redshift.cluster_identifier", cluster_identifier)
            span._set_attribute("clusteridentifier", cluster_identifier)
    elif endpoint_name == "s3":
        bucket_name = params.get("Bucket")
        if bucket_name:
            span._set_attribute("aws.s3.bucket_name", bucket_name)
            span._set_attribute("bucketname", bucket_name)

    elif endpoint_name == "sns":
        topic_arn = params.get("TopicArn")
        if topic_arn:
            # example topicArn: arn:aws:sns:sa-east-1:1234:topicname
            span._set_attribute("aws.sns.topic_arn", topic_arn)
            topicname = topic_arn.split(":")[-1]
            aws_account = topic_arn.split(":")[-2]
            span._set_attribute("aws_account", aws_account)
            span._set_attribute("topicname", topicname)

    elif endpoint_name == "sqs":
        queue_name = params.get("QueueName", "")
        queue_url = params.get("QueueUrl")
        if queue_url and (queue_url.startswith("sqs:") or queue_url.startswith("http")):
            # example queue_url: https://sqs.sa-east-1.amazonaws.com/12345678/queuename
            queue_name = queue_url.split("/")[-1]
            aws_account = queue_url.split("/")[-2]
            span._set_attribute("aws_account", aws_account)
            span._set_attribute(SQS_QUEUE_URL, queue_url)
        span._set_attribute("aws.sqs.queue_name", queue_name)
        span._set_attribute("queuename", queue_name)

    elif endpoint_name == "lambda":
        function_name = params.get("FunctionName", "")
        span._set_attribute("functionname", function_name)
        # Only a full ARN names the function's account and region:
        # arn:<partition>:lambda:<region>:<account>:function:<name>[:<qualifier>]
        if isinstance(function_name, str) and function_name.startswith("arn:"):
            span._set_attribute(FUNCTION_ARN, function_name)

    elif endpoint_name == "events":
        rule_name = params.get("Name", "")
        span._set_attribute("rulename", rule_name)

    elif endpoint_name == "states":
        state_machine_arn = params.get("stateMachineArn", "")
        span._set_attribute("statemachinearn", state_machine_arn)
        if state_machine_arn:
            span._set_attribute(STEP_FUNCTIONS_STATE_MACHINE_ARN, state_machine_arn)


def get_aws_partition(region_name: str) -> str:
    """Determine AWS partition from region name."""
    if region_name.startswith("cn-"):
        return "aws-cn"
    elif region_name.startswith("us-gov-"):
        return "aws-us-gov"

    return "aws"


AWSREGION = "aws.region"
REGION = "region"
PARTITION = "aws.partition"
AGENT = "aws.agent"
OPERATION = "aws.operation"
SQS_QUEUE_URL = "aws.sqs.queue.url"
STREAM_ARN = "aws.stream.arn"
STEP_FUNCTIONS_STATE_MACHINE_ARN = "aws.step_functions.state_machine.arn"
TABLE_ARN = "aws.table.arn"
FUNCTION_ARN = "aws.function.arn"
