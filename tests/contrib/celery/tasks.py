from celery import Celery


app = Celery("tasks")


@app.task(name="tests.contrib.celery.tasks.fn_a")
def fn_a():
    print("apples are done")
    return "apples"


@app.task(name="tests.contrib.celery.tasks.fn_b")
def fn_b():
    print("oranges are done")
    return "oranges"
