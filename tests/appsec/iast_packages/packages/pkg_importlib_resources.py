"""
importlib-resources==6.4.0

https://pypi.org/project/importlib-resources/
"""
import os
import shutil

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

        # Ensure the data directory and file exist
        data_dir = "data"
        file_path = os.path.join(data_dir, resource_name)

        if not os.path.exists(data_dir):
            os.makedirs(data_dir)
        if not os.path.exists(file_path):
            with open(file_path, "w") as f:
                f.write("This is the default content of the file.")

        try:
            content = resources.files(data_dir).joinpath(resource_name).read_text()
            result_output = f"Content of {resource_name}:\n{content}"
        except FileNotFoundError:
            result_output = f"Resource {resource_name} not found."

        response.result1 = result_output
    except Exception as e:
        response.result1 = f"Error: {str(e)}"
    finally:
        if data_dir and os.path.exists(data_dir):
            try:
                shutil.rmtree(data_dir)
            except Exception:
                pass

    return response.json()
