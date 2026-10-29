"""sns-publisher: publishes to an SNS topic (source of the SNS -> SQS topology).

Delivery to SQS happens through the queue subscription created by
setup/setup_sns.py; this service only talks to SNS.
"""

import os
import sys


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import common


topic = os.environ.get("TOPIC", "sns-demo-topic")
count = int(os.environ.get("MSGS", "3"))

sns = common.boto3_client("sns")
topic_arn = sns.create_topic(Name=topic)["TopicArn"]
for i in range(count):
    sns.publish(TopicArn=topic_arn, Subject="demo", Message="msg-%d" % i)
print("sns-publisher published %d messages to %s" % (count, topic_arn))
