import random
import time

from celery import Celery


app = Celery("consumer", broker="redis://redis", backend="redis://redis")


def consumer_work():
    time.sleep(random.uniform(0, 0.02))


@app.task
def add(x, y):
    for _ in range(0, 5):
        consumer_work()
    return x + y
