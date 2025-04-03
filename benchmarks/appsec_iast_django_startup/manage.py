import os
import sys

# TODO: CV2 fails in the CI with ImportError: libGL.so.1: cannot open shared object file: No such file or directory
# from cv2 import *  # noqa: F401, F403
from django import *  # noqa: F401, F403
import numpy as np  # noqa: F401, F403
import pandas as pd  # noqa: F401, F403


def main():
    """Run administrative tasks."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
