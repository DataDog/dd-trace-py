"""sqs-src: sends demo messages to an SQS queue (pipeline source)."""

import os
import sys


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import common


queue = os.environ.get("QUEUE", "sqs-a")
count = int(os.environ.get("MSGS", "3"))

sqs = common.boto3_client("sqs")
queue_url = sqs.get_queue_url(QueueName=queue)["QueueUrl"]
for i in range(count):
    sqs.send_message(QueueUrl=queue_url, MessageBody="msg-%d" % i)
print("sqs-src sent %d messages to %s" % (count, queue))
