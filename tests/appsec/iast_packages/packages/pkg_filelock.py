"""
filelock==3.14.0

https://pypi.org/project/filelock/
"""

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
        with tempfile.TemporaryDirectory() as tmp_dir:
            lock = FileLock(os.path.join(tmp_dir, os.path.basename(file_name)), timeout=1)
            try:
                with lock.acquire(timeout=1):
                    result_output = f"Lock acquired for file: {file_name}"
            except Timeout:
                result_output = f"Timeout: Could not acquire lock for file: {file_name}"

        response.result1 = result_output
    except Exception as e:
        response.result1 = f"Error: {str(e)}"
    return response.json()
