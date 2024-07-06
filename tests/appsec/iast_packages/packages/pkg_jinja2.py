"""
jinja2==3.1.4

https://pypi.org/project/jinja2/
"""
from flask import Blueprint
from flask import request

from .utils import ResultResponse


pkg_jinja2 = Blueprint("package_jinja2", __name__)


@pkg_jinja2.route("/jinja2")
def pkg_jinja2_view():
    from jinja2 import Template

    response = ResultResponse(request.args.get("package_param"))

    try:
        param_value = request.args.get("package_param", "default-value")

        template_string = "Hello, {{ name }}!"
        template = Template(template_string)
        rendered_output = template.render(name=param_value)

        response.result1 = rendered_output
    except Exception as e:
        response.result1 = f"Error: {str(e)}"

    return response.json()


@pkg_jinja2.route("/jinja2_propagation")
def pkg_jinja2_propagation_view():
    from jinja2 import Template

    from ddtrace.appsec._iast._taint_tracking import is_pyobject_tainted

    response = ResultResponse(request.args.get("package_param"))
    if not is_pyobject_tainted(response.package_param):
        response.result1 = "Error: package_param is not tainted"
        return response.json()

    try:
        param_value = request.args.get("package_param", "default-value")

        template_string = "Hello, {{ name }}!"
        template = Template(template_string)
        rendered_output = template.render(name=param_value)

        response.result1 = (
            "OK"
            if is_pyobject_tainted(rendered_output)
            else "Error: rendered_output is not tainted: %s" % rendered_output
        )
    except Exception as e:
        response.result1 = f"Error: {str(e)}"

    return response.json()
