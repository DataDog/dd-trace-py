"""Create the SQS queues used by the demo topologies.

Run with plain python3 (NOT ddtrace-run) so no sides are recorded:
    python3 setup/setup_sqs.py
"""

import sys


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import common


QUEUES = ["sqs-a", "sqs-b"]

sqs = common.boto3_client("sqs")
for queue in QUEUES:
    url = sqs.create_queue(QueueName=queue)["QueueUrl"]
    print("sqs queue ready: %s (%s)" % (queue, url))
