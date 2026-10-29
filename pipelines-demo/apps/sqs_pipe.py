"""sqs-pipe: consumes QUEUE_IN and produces to QUEUE_OUT (in-process link)."""

import os
import sys
import time


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import common


queue_in = os.environ.get("QUEUE_IN", "sqs-a")
queue_out = os.environ.get("QUEUE_OUT", "sqs-b")
target = int(os.environ.get("MSGS", "3"))

sqs = common.boto3_client("sqs")
url_in = sqs.get_queue_url(QueueName=queue_in)["QueueUrl"]
url_out = sqs.get_queue_url(QueueName=queue_out)["QueueUrl"]

piped = 0
deadline = time.time() + 60
while piped < target and time.time() < deadline:
    response = sqs.receive_message(QueueUrl=url_in, MaxNumberOfMessages=10, WaitTimeSeconds=5)
    for message in response.get("Messages", []):
        sqs.send_message(QueueUrl=url_out, MessageBody=message["Body"])
        sqs.delete_message(QueueUrl=url_in, ReceiptHandle=message["ReceiptHandle"])
        piped += 1
print("sqs-pipe piped %d messages %s -> %s" % (piped, queue_in, queue_out))
