"""Wire SNS -> SQS: topic, queue, and the queue subscription to the topic.

Non-raw delivery on purpose: the SNS message envelope (TopicArn) is what lets
the tracer classify the subscriber's consume side as ``sns``/<TopicArn>.

Run with plain python3 (NOT ddtrace-run) so no sides are recorded:
    python3 setup/setup_sns.py
"""

import sys


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import common


TOPIC = "sns-demo-topic"
QUEUE = "sns-demo-queue"

sns = common.boto3_client("sns")
sqs = common.boto3_client("sqs")

topic_arn = sns.create_topic(Name=TOPIC)["TopicArn"]
queue_url = sqs.create_queue(QueueName=QUEUE)["QueueUrl"]
queue_arn = sqs.get_queue_attributes(QueueUrl=queue_url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]

sns.subscribe(TopicArn=topic_arn, Protocol="sqs", Endpoint=queue_arn)
print("sns -> sqs wired: %s -> %s" % (topic_arn, queue_arn))
