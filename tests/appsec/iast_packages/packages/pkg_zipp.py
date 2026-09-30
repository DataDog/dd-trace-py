"""
zipp==3.11.0

https://pypi.org/project/zipp/
"""

import os
import tempfile
import zipfile

from flask import Blueprint
from flask import request

from .utils import ResultResponse


pkg_zipp = Blueprint("package_zipp", __name__)


@pkg_zipp.route("/zipp")
def pkg_zipp_view():
    import zipp

    response = ResultResponse(request.args.get("package_param"))

    try:
        zip_param = request.args.get("package_param", "example.zip")

        # Private directory per request: xdist workers share a cwd, so under a fixed name one
        # request's cleanup removes the archive another request is still reading.
        with tempfile.TemporaryDirectory() as tmp_dir:
            zip_file_path = os.path.join(tmp_dir, os.path.basename(zip_param))

            try:
                # Create an example zip file
                with zipfile.ZipFile(zip_file_path, "w") as zip_file:
                    zip_file.writestr("example.txt", "This is an example file.")

                # Read the contents of the zip file using zipp. Report member names rather than
                # full paths so the result does not depend on where the archive lives.
                zip_path = zipp.Path(zip_file_path)
                contents = [file.name for file in zip_path.iterdir()]
                result_output = f"Contents of {os.path.basename(zip_file_path)}: {contents}"
            except Exception as e:
                result_output = f"Error: {str(e)}"

        response.result1 = result_output
    except Exception as e:
        response.result1 = f"Error: {str(e)}"

    return response.json()
