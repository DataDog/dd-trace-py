"""
filelock==3.7.1

https://pypi.org/project/filelock/
"""

import contextlib
import os
import tempfile

from flask import Blueprint
from flask import request

from .utils import ResultResponse


pkg_filelock = Blueprint("package_filelock", __name__)


@pkg_filelock.route("/filelock")
def pkg_filelock_view():
    from filelock import FileLock
    from filelock import Timeout

    response = ResultResponse(request.args.get("package_param"))

    try:
        # Use package_param to specify the file to lock
        file_name = request.args.get("package_param", "default.lock")

        # Example usage of filelock package
        lock_path = os.path.join(tempfile.gettempdir(), os.path.basename(file_name))
        lock = FileLock(lock_path, timeout=1)
        try:
            with lock.acquire(timeout=1):
                result_output = f"Lock acquired for file: {file_name}"
        except Timeout:
            result_output = f"Timeout: Could not acquire lock for file: {file_name}"
        finally:
            with contextlib.suppress(OSError):
                os.remove(lock_path)

        response.result1 = result_output
    except Exception as e:
        response.result1 = f"Error: {str(e)}"
    return response.json()
