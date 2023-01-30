import confluent_kafka

from ddtrace.vendor import wrapt

from ...pin import Pin
from ..trace_utils import unwrap


def patch():
    if getattr(confluent_kafka, "_datadog_patch", False):
        return
    setattr(confluent_kafka, "_datadog_patch", True)

    _w = wrapt.wrap_function_wrapper

    _w("confluent_kafka", "Producer", traced_producer)
    Pin(service=None).onto(confluent_kafka.Producer)


def unpatch():
    if getattr(confluent_kafka, "_datadog_patch", False):
        setattr(confluent_kafka, "_datadog_patch", False)

    unwrap(confluent_kafka, "Producer")


def traced_producer(func, instance, args, kwargs):
    producer = func(*args, **kwargs)
    pin = Pin.get_from(instance)
    if pin:
        pin.onto(producer)
    return producer
