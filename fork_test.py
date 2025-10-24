import os
import sys

from ddtrace.internal import atexit


def exit_handler():
    print(f"{os.getpid()} exiting")


atexit.register(exit_handler)

child_pid = os.fork()

if child_pid == 0:
    print(f"{os.getpid()} hello from child")

else:
    print(f"{os.getpid()} hello from parent")

    pid, status = os.waitpid(child_pid, 0)
    sys.exit(os.WEXITSTATUS(status))
