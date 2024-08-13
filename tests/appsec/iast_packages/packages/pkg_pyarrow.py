"""
pyarrow==16.1.0

https://pypi.org/project/pyarrow/
"""
import os

from flask import Blueprint
from flask import jsonify
from flask import request

from .utils import ResultResponse


pkg_pyarrow = Blueprint("package_pyarrow", __name__)


@pkg_pyarrow.route("/pyarrow")
def pkg_pyarrow_view():
    import pyarrow as pa
    import pyarrow.parquet as pq

    response = ResultResponse(request.args.get("package_param"))

    try:
        param_value = request.args.get("package_param", "default-value")
        table_path = "example.parquet"

        try:
            # Create a simple table
            data = {"column1": [param_value], "column2": [1]}
            table = pa.table(data)

            # Write the table to a Parquet file
            pq.write_table(table, table_path)

            # Read the table back from the Parquet file
            read_table = pq.read_table(table_path)
            result_output = f"Table data: {read_table.to_pandas().to_dict()}"

            # Clean up the created Parquet file
            if os.path.exists(table_path):
                os.remove(table_path)
        except Exception as e:
            result_output = f"Error: {str(e)}"

        response.result1 = result_output
    except Exception as e:
        response.result1 = f"Error: {str(e)}"

    return jsonify(response.json())
