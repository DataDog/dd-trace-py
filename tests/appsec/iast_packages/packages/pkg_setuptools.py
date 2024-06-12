"""
setuptools==70.0.0

https://pypi.org/project/setuptools/
"""
from flask import Blueprint
from flask import request

from .utils import ResultResponse


pkg_setuptools = Blueprint("package_setuptools", __name__)


@pkg_setuptools.route("/setuptools")
def pkg_setuptools_view():
    import setuptools

    response = ResultResponse(request.args.get("package_param"))

    try:
        # Ejemplo de uso común del paquete setuptools
        distribution = setuptools.Distribution(
            {
                "name": "example_package",
                "version": "0.1",
                "description": "An example package",
                "packages": setuptools.find_packages(),
            }
        )
        distribution_metadata = distribution.metadata

        response.result1 = {
            "name": distribution_metadata.get_name(),
            "description": distribution_metadata.get_description(),
        }
    except Exception as e:
        response.result1 = str(e)

    return response.json()
