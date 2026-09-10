"""
virtualenv==20.26.2

https://pypi.org/project/virtualenv/
"""

import os
import tempfile

from flask import Blueprint
from flask import request

from .utils import ResultResponse


pkg_virtualenv = Blueprint("package_virtualenv", __name__)


@pkg_virtualenv.route("/virtualenv")
def pkg_virtualenv_view():
    import virtualenv

    response = ResultResponse(request.args.get("package_param"))

    try:
        env_name = request.args.get("package_param", "default-env")
        # Private directory per request: xdist workers share a cwd, so under a fixed path one
        # request's cleanup deletes the environment another request is still creating.
        with tempfile.TemporaryDirectory() as tmp_dir:
            # basename so the env cannot land outside tmp_dir and escape its cleanup.
            env_path = os.path.join(tmp_dir, os.path.basename(env_name) or "env")

            try:
                # Create a virtual environment
                virtualenv.cli_run([env_path])
                result_output = "Virtual environment created at replaced_path"

                # Optionally, list the contents of the virtual environment's bin/Scripts directory
                _ = os.path.join(env_path, "bin" if os.name != "nt" else "Scripts")
                result_output += "\nContents of replaced_path: replaced_contents"

            except Exception as e:
                result_output = f"Error: {str(e)}"

        response.result1 = result_output
    except Exception as e:
        response.result1 = f"Error: {str(e)}"

    return response.json()
