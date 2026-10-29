"""pubsub-sink: consumes subscription SUB_IN (pipeline end).

get_subscription is called at startup so the tracer learns the subscription
-> topic mapping (CONTRACT.md section 1 pubsub notes).
"""

import os
import sys
import threading


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import common
from google.cloud import pubsub_v1


project = common.PUBSUB_PROJECT
subscription_in = os.environ.get("SUB_IN", "ps-sub-b")
target = int(os.environ.get("MSGS", "3"))

subscriber = pubsub_v1.SubscriberClient()
subscription_path = subscriber.subscription_path(project, subscription_in)
subscription = subscriber.get_subscription(request={"subscription": subscription_path})
topic_in = subscription.topic.rsplit("/", 1)[-1]

done = threading.Event()
received = {"count": 0}


def handle(message):
    message.ack()
    received["count"] += 1
    if received["count"] >= target:
        done.set()


future = subscriber.subscribe(subscription_path, handle)
done.wait(120)
future.cancel()
print("pubsub-sink received %d messages from topic %s" % (received["count"], topic_in))
