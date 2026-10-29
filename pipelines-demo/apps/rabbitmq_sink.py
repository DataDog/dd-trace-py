"""rmq-sink: consumes a RabbitMQ queue via kombu (pipeline end)."""

import os
import sys
import threading
import time


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import common
from kombu import Connection
from kombu import Consumer
from kombu import Queue


queue = os.environ.get("QUEUE", "rmq-b")
target = int(os.environ.get("MSGS", "3"))

received = {"count": 0}
done = threading.Event()


def handle(body, message):
    message.ack()
    received["count"] += 1
    if received["count"] >= target:
        done.set()


with Connection(common.RABBITMQ_URL) as connection:
    queues = [Queue(queue, routing_key=queue)]
    with Consumer(connection, queues=queues, accept=["json"], callbacks=[handle]):
        deadline = time.time() + 60
        while received["count"] < target and time.time() < deadline:
            connection.drain_events(timeout=5)
print("rmq-sink received %d messages from %s" % (received["count"], queue))
