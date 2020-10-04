from celery import Celery, VERSION

from .base import BROKER_URL, BACKEND_URL

# Celery needs to be configured so as to not accept pickle messages since we run
# this as root

# Setting name change since 4.0
ACCEPT_CONTENT_CONFIG = "CELERY_ACCEPT_CONTENT" if VERSION < (4, 0, 0) else "accept_content"

app = Celery("celery.test_tasks", broker=BROKER_URL, backend=BACKEND_URL)

app.conf[ACCEPT_CONTENT_CONFIG] = ["json"]


@app.task
def add(x, y):
    return x + y
