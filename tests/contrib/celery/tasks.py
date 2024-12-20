from celery import Celery


app = Celery("tasks")


@app.task(name="tests.contrib.celery.tasks.fn_a")
def fn_a():
    return "a"


@app.task(name="tests.contrib.celery.tasks.fn_b")
def fn_b():
    return "b"
