"""
PyYAML==6.0.1

https://pypi.org/project/PyYAML/
"""

import json

from flask import Blueprint
from flask import request

from .utils import ResultResponse


pkg_pyyaml = Blueprint("package_pyyaml", __name__)


@pkg_pyyaml.route("/PyYAML")
def pkg_pyyaml_view():
    import yaml

    response = ResultResponse(request.args.get("package_param"))
    rs = json.loads(response.package_param)
    yaml_string = yaml.dump(rs)
    response.result1 = yaml.safe_load(yaml_string)
    response.result2 = yaml.dump(response.result1)
    return response.json()


@pkg_pyyaml.route("/PyYAML_propagation")
def pkg_pyyaml_propagation_view():
    import yaml

    from ddtrace.appsec._iast._taint_tracking._taint_objects_base import is_pyobject_tainted

    response = ResultResponse(request.args.get("package_param"))
    if not is_pyobject_tainted(response.package_param):
        response.result1 = "Error: package_param is not tainted"
        return response.json()

    rs = json.loads(response.package_param)
    yaml_string = yaml.dump(rs)
    response.result1 = (
        "OK" if is_pyobject_tainted(yaml_string) else "Error: yaml_string is not tainted: %s" % yaml_string
    )
    return response.json()
