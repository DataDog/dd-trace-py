"""
packaging==24.0
https://pypi.org/project/packaging/
"""
from flask import Blueprint
from flask import request

from .utils import ResultResponse


pkg_packaging = Blueprint("package_packaging", __name__)


@pkg_packaging.route("/packaging")
def pkg_packaging_view():
    from packaging.requirements import Requirement
    from packaging.specifiers import SpecifierSet
    from packaging.version import Version

    response = ResultResponse(request.args.get("package_param"))

    try:
        version = Version("1.2.3")
        specifier = SpecifierSet(">=1.0.0")
        requirement = Requirement("example-package>=1.0.0")

        is_version_valid = version in specifier
        requirement_str = str(requirement)

        response.result1 = {
            "version": str(version),
            "specifier": str(specifier),
            "is_version_valid": is_version_valid,
            "requirement": requirement_str,
        }
    except Exception as e:
        response.result1 = str(e)

    return response.json()
