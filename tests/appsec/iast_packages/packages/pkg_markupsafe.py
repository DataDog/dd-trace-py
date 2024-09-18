"""
markupsafe==2.1.5

https://pypi.org/project/markupsafe/
"""
from flask import Blueprint
from flask import request
from jinja2 import Template
from markupsafe import escape

from .utils import ResultResponse


pkg_markupsafe = Blueprint("package_markupsafe", __name__)


@pkg_markupsafe.route("/markupsafe")
def pkg_markupsafe_view():
    response = ResultResponse(request.args.get("package_param"))

    try:
        param_value = request.args.get("package_param", "default-value")
        safe_value = escape(param_value)
        template_string = "Hello, {{ name }}!"
        template = Template(template_string)
        rendered_output = template.render(name=safe_value)

        response.result1 = rendered_output
    except Exception as e:
        response.result1 = f"Error: {str(e)}"

    return response.json()
