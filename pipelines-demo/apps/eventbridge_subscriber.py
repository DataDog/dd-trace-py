"""eb-subscriber: polls the SQS queue targeted by the EventBridge rule.

The consume side is classified by the tracer as ``eventbridge``/<detail-type>
from the event envelope delivered to the queue (see PIPELINE_TOPOLOGIES.md
section 5), which is what joins this consumer to the publisher's produce side.
"""

import os
import sys
import time


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import common


queue = os.environ.get("QUEUE", "eb-demo-queue")
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
print("eb-subscriber received %d messages from %s" % (received, queue))
