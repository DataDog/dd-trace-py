# Create your tasks here

from celery import shared_task
from demoapp.models import Widget


@shared_task
def add(x, y):
    return x + y


@shared_task
def mul(x, y):
    return x * y


@shared_task
def xsum(numbers):
    return sum(numbers)


@shared_task
def count_widgets():
    return Widget.objects.count()


@shared_task
def rename_widget(widget_id, name):
    w = Widget.objects.get(id=widget_id)
    w.name = name
    w.save()


@shared_task
def make_request(url):
    import requests
    from requests.packages.urllib3.util.ssl_ import create_urllib3_context

    create_urllib3_context()
    return requests.get(url).text
