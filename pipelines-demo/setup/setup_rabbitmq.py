"""Declare the RabbitMQ queues used by the demo topologies.

Run with plain python3 (NOT ddtrace-run) so no sides are recorded:
    python3 setup/setup_rabbitmq.py
"""

import sys


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import common
from kombu import Connection
from kombu import Queue


QUEUES = ["rmq-a", "rmq-b"]

with Connection(common.RABBITMQ_URL) as connection:
    channel = connection.channel()
    for queue in QUEUES:
        Queue(queue, routing_key=queue).maybe_declare(channel)
        print("rabbitmq queue declared: %s" % queue)
