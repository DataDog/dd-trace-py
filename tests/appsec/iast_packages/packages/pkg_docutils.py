"""
docutils==0.21.2

https://pypi.org/project/docutils/
"""
from flask import Blueprint
from flask import jsonify
from flask import request

from .utils import ResultResponse


pkg_docutils = Blueprint("package_docutils", __name__)


@pkg_docutils.route("/docutils")
def pkg_docutils_view():
    import docutils.core

    response = ResultResponse(request.args.get("package_param"))

    try:
        rst_content = request.args.get("package_param", "Hello, **world**!")

        try:
            # Convert reStructuredText to HTML
            html_output = docutils.core.publish_string(rst_content, writer_name="html").decode("utf-8")
            if html_output:
                result_output = "Conversion successful!"
            else:
                result_output = "Conversion failed!"
        except Exception as e:
            result_output = f"Error: {str(e)}"

        response.result1 = result_output
    except Exception as e:
        response.result1 = f"Error: {str(e)}"

    return jsonify(response.json())


@pkg_docutils.route("/docutils_propagation")
def pkg_docutils_propagation_view():
    import docutils.core

    from ddtrace.appsec._iast._taint_tracking import is_pyobject_tainted

    response = ResultResponse(request.args.get("package_param"))

    try:
        rst_content = request.args.get("package_param", "Hello, **world**!")
        if not is_pyobject_tainted(rst_content):
            response.result1 = "Error: package_param is not tainted"
            return jsonify(response.json())

        try:
            # Convert reStructuredText to HTML
            html_output = docutils.core.publish_string(rst_content, writer_name="html").decode("utf-8")
            result_output = (
                "OK" if is_pyobject_tainted(html_output) else f"Error: html_output is not tainted: {html_output}"
            )
        except Exception as e:
            result_output = f"Error: {str(e)}"
    except Exception as e:
        result_output = f"Error: {str(e)}"

    response.result1 = result_output
    return jsonify(response.json())
