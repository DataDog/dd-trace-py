"""kafka-sqs-pipe: consumes Kafka TOPIC_IN and produces to an SQS QUEUE.

Cross-system mixed topology: the in-process link glues a ``kafka`` consume
side to an ``sqs`` produce side inside one service.
"""

import os
import sys
import time


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import common
from confluent_kafka import Consumer


topic_in = os.environ.get("TOPIC_IN", "topic-a")
group = os.environ.get("GROUP", "mixed-pipe-group")
queue = os.environ.get("QUEUE", "sqs-a")
target = int(os.environ.get("MSGS", "3"))

sqs = common.boto3_client("sqs")
queue_url = sqs.get_queue_url(QueueName=queue)["QueueUrl"]

consumer = Consumer(
    {
        "bootstrap.servers": common.KAFKA_BOOTSTRAP,
        "group.id": group,
        "auto.offset.reset": "earliest",
    }
)
consumer.subscribe([topic_in])

piped = 0
deadline = time.time() + 60
while piped < target and time.time() < deadline:
    message = consumer.poll(1.0)
    if message is None or message.error():
        continue
    sqs.send_message(QueueUrl=queue_url, MessageBody=message.value().decode("utf-8", "replace"))
    piped += 1
consumer.close()
print("kafka-sqs-pipe piped %d messages %s -> %s" % (piped, topic_in, queue))
