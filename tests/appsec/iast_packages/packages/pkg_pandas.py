"""
pandas==1.3.5

https://pypi.org/project/pandas/
"""
import os

from flask import Blueprint
from flask import request

from .utils import ResultResponse


pkg_pandas = Blueprint("package_pandas", __name__)


@pkg_pandas.route("/pandas")
def pkg_pandas_view():
    import pandas as pd

    response = ResultResponse(request.args.get("package_param"))

    try:
        param_value = request.args.get("package_param", "default-value")

        # Create a DataFrame
        df = pd.DataFrame({"Column1": [param_value]})

        # Save the DataFrame to a CSV file
        file_path = "example.csv"
        df.to_csv(file_path, index=False)

        # Read back the value from the file to ensure it was written correctly
        df_read = pd.read_csv(file_path)
        read_value = df_read.iloc[0]["Column1"]

        # Clean up the created file
        os.remove(file_path)

        result_output = f"Written value: {read_value}"

        response.result1 = result_output
    except Exception as e:
        response.result1 = f"Error: {str(e)}"

    return response.json()
