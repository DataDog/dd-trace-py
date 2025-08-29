from tasks_with_error import err_task
from tasks_with_error import fn_task


t = fn_task.s().on_error(err_task.s()).delay()

try:
    t.get(timeout=30)
except ValueError:
    pass
