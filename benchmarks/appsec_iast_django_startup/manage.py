import os
import sys

# TODO: CV2 fails in the CI with ImportError: libGL.so.1: cannot open shared object file: No such file or directory
# from cv2 import *  # noqa: F401, F403
from django import *  # noqa: F401, F403
from scipy import *  # noqa: F401, F403


# TODO: we want to check django first
# from kombu import *  # noqa: F401, F403
# from matplotlib import *  # noqa: F401, F403
# from nibabel import *  # noqa: F401, F403
# from pandas import *  # noqa: F401, F403
# from PIL import *  # noqa: F401, F403


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
