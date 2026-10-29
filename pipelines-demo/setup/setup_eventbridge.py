"""Wire EventBridge -> SQS: rule matching Source "demo.src", SQS target queue.

Run with plain python3 (NOT ddtrace-run) so no sides are recorded:
    python3 setup/setup_eventbridge.py
"""

import sys


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import common


BUS = "default"
RULE = "demo-rule"
QUEUE = "eb-demo-queue"

events = common.boto3_client("events")
sqs = common.boto3_client("sqs")

queue_url = sqs.create_queue(QueueName=QUEUE)["QueueUrl"]
queue_arn = sqs.get_queue_attributes(QueueUrl=queue_url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]

events.put_rule(Name=RULE, EventBusName=BUS, EventPattern='{"source": ["demo.src"]}')
events.put_targets(Rule=RULE, EventBusName=BUS, Targets=[{"Id": "to-sqs", "Arn": queue_arn}])
print("eventbridge -> sqs wired: rule %s -> %s" % (RULE, queue_arn))
