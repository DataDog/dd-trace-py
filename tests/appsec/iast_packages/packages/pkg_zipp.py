"""
zipp==3.11.0

https://pypi.org/project/zipp/
"""
import os
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

        try:
            # Create an example zip file
            with zipfile.ZipFile(zip_param, "w") as zip_file:
                zip_file.writestr("example.txt", "This is an example file.")

            # Read the contents of the zip file using zipp
            zip_path = zipp.Path(zip_param)
            contents = [str(file) for file in zip_path.iterdir()]
            result_output = f"Contents of {zip_param}: {contents}"

            # Clean up the created zip file
            if os.path.exists(zip_param):
                os.remove(zip_param)
        except Exception as e:
            result_output = f"Error: {str(e)}"

        response.result1 = result_output
    except Exception as e:
        response.result1 = f"Error: {str(e)}"

    return response.json()
