"""rmq-pipe: consumes QUEUE_IN and produces to QUEUE_OUT via kombu.

Uses the default exchange with routing key = queue name on both sides; the
produce happens on the Consumer.receive callback thread that recorded the
consume, so the in-process link is captured.
"""

import os
import sys
import threading
import time


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import common
from kombu import Connection
from kombu import Consumer
from kombu import Producer
from kombu import Queue


queue_in = os.environ.get("QUEUE_IN", "rmq-a")
queue_out = os.environ.get("QUEUE_OUT", "rmq-b")
target = int(os.environ.get("MSGS", "3"))

received = {"count": 0}
done = threading.Event()


def handle(body, message):
    producer.publish(body, routing_key=queue_out, serializer="json")
    message.ack()
    received["count"] += 1
    if received["count"] >= target:
        done.set()


with Connection(common.RABBITMQ_URL) as connection:
    producer = Producer(connection)
    queues = [Queue(queue_in, routing_key=queue_in)]
    with Consumer(connection, queues=queues, accept=["json"], callbacks=[handle]):
        deadline = time.time() + 60
        while received["count"] < target and time.time() < deadline:
            connection.drain_events(timeout=5)
print("rmq-pipe piped %d messages %s -> %s" % (received["count"], queue_in, queue_out))
