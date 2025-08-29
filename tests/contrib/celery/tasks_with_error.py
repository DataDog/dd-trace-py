from celery import Celery


app = Celery("tasks_with_error")


@app.task(name="tests.contrib.celery.tasks_with_error.fn_task")
def fn_task():
    raise ValueError("Task failed")


@app.task(name="tests.contrib.celery.tasks_with_error.err_task")
def err_task(task, err, tb):
    from ddtrace import tracer

    span = tracer.start_span("err_task_custom_span")
    span.finish()
    return "error happened"
