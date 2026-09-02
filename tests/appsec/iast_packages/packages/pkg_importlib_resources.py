"""
importlib-resources==6.4.0

https://pypi.org/project/importlib-resources/
"""

import importlib
import os
import shutil
import sys
import uuid

from flask import Blueprint
from flask import request

from .utils import ResultResponse


pkg_importlib_resources = Blueprint("package_importlib_resources", __name__)


@pkg_importlib_resources.route("/importlib-resources")
def pkg_importlib_resources_view():
    import importlib_resources as resources

    response = ResultResponse(request.args.get("package_param"))
    data_dir = None
    try:
        resource_name = request.args.get("package_param", "default.txt")

        # Unique per request: xdist workers share a cwd, so under a fixed name one request's
        # cleanup deletes the file another is still reading. It has to stay under the cwd for
        # resources.files() to resolve it as a namespace package, hence the cache invalidation.
        data_dir = f"data_{uuid.uuid4().hex}"
        file_path = os.path.join(data_dir, resource_name)

        os.makedirs(data_dir, exist_ok=True)
        with open(file_path, "w") as f:
            f.write("This is the default content of the file.")
        importlib.invalidate_caches()

        try:
            content = resources.files(data_dir).joinpath(resource_name).read_text()
            result_output = f"Content of {resource_name}:\n{content}"
        except FileNotFoundError:
            result_output = f"Resource {resource_name} not found."

        response.result1 = result_output
    except Exception as e:
        response.result1 = f"Error: {str(e)}"
    finally:
        if data_dir:
            # resources.files() leaves a namespace module behind for each unique name.
            sys.modules.pop(data_dir, None)
            shutil.rmtree(data_dir, ignore_errors=True)

    return response.json()
