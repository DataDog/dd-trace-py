from celery import Celery

from .base import AMQP_BROKER_URL
from .base import BACKEND_URL
from .base import BROKER_URL


redis_celery_app = Celery(
    "mul_celery",
    broker=BROKER_URL,
    backend=BACKEND_URL,
)


@redis_celery_app.task
def multiply(x, y):
    return x * y


amqp_celery_app = Celery(
    "add_celery",
    broker=AMQP_BROKER_URL,
    backend="rpc://",
)


@amqp_celery_app.task
def add(x, y):
    return x + y
