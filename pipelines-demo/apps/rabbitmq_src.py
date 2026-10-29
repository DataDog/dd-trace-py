"""rmq-src: publishes to a RabbitMQ queue via kombu (pipeline source).

Publishes through the default exchange with routing key = queue name, so the
tracer records the ``rabbitmq`` side against the queue name.
"""

import os
import sys


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import common
from kombu import Connection
from kombu import Producer


queue = os.environ.get("QUEUE", "rmq-a")
count = int(os.environ.get("MSGS", "3"))

with Connection(common.RABBITMQ_URL) as connection:
    producer = Producer(connection)
    for i in range(count):
        producer.publish({"i": i}, routing_key=queue, serializer="json")
print("rmq-src published %d messages to %s" % (count, queue))
