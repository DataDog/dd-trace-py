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
def pkg_idna_view():
    import yaml

    response = ResultResponse(request.args.get("package_param"))
    rs = json.loads(response.package_param)
    yaml_string = yaml.dump(rs)
    response.result1 = yaml.safe_load(yaml_string)
    response.result2 = yaml.dump(response.result1)
    return response.json()
