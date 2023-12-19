"""
PyYAML==6.0.1

https://pypi.org/project/PyYAML/
"""
from flask import Blueprint
from flask import request
import yaml

from .utils import ResultResponse


pkg_pyyaml = Blueprint("package_pyyaml", __name__)


@pkg_pyyaml.route("/PyYAML")
def pkg_idna_view():
    response = ResultResponse(request.args.get("package_param"))
    response.result1 = yaml.safe_load(response.package_param)
    response.result2 = yaml.dump(response.result1)
    return response.json()
