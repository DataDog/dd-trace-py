"""sns-subscriber: polls the SQS queue subscribed to the SNS topic.

The consume side is classified by the tracer as ``sns``/<TopicArn> from the SNS
message envelope (see PIPELINE_TOPOLOGIES.md section 5), which is what joins
this consumer to the SNS topic produce side even though the transport is SQS.
"""

import os
import sys
import time


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import common


queue = os.environ.get("QUEUE", "sns-demo-queue")
target = int(os.environ.get("MSGS", "3"))

sqs = common.boto3_client("sqs")
queue_url = sqs.get_queue_url(QueueName=queue)["QueueUrl"]

received = 0
deadline = time.time() + 60
while received < target and time.time() < deadline:
    response = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=10, WaitTimeSeconds=5)
    for message in response.get("Messages", []):
        sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=message["ReceiptHandle"])
        received += 1
print("sns-subscriber received %d messages from %s" % (received, queue))
