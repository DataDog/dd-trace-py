"""
openpyxl==3.0.10

https://pypi.org/project/openpyxl/
"""
import os

from flask import Blueprint
from flask import request

from .utils import ResultResponse


pkg_openpyxl = Blueprint("package_openpyxl", __name__)


@pkg_openpyxl.route("/openpyxl")
def pkg_openpyxl_view():
    import openpyxl

    response = ResultResponse(request.args.get("package_param"))

    try:
        param_value = request.args.get("package_param", "default-value")

        # Create a workbook and select the active worksheet
        wb = openpyxl.Workbook()
        ws = wb.active

        # Write the parameter value to the first cell
        ws["A1"] = param_value

        # Save the workbook to a file
        file_path = "example.xlsx"
        wb.save(file_path)

        # Read back the value from the file to ensure it was written correctly
        wb_read = openpyxl.load_workbook(file_path)
        ws_read = wb_read.active
        read_value = ws_read["A1"].value

        # Clean up the created file
        os.remove(file_path)

        result_output = f"Written value: {read_value}"

        response.result1 = result_output
    except Exception as e:
        response.result1 = f"Error: {str(e)}"

    return response.json()
