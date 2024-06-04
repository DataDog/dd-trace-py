"""
cffi==1.16.0

https://pypi.org/project/cffi/
"""
from flask import Blueprint
from flask import request

from .utils import ResultResponse


pkg_cffi = Blueprint("package_cffi", __name__)


@pkg_cffi.route("/cffi")
def pkg_cffi_view():
    import cffi

    response = ResultResponse(request.args.get("package_param"))

    try:
        ffi = cffi.FFI()
        ffi.cdef("int add(int, int);")
        C = ffi.verify(
            """
            int add(int x, int y) {
                return x + y;
            }
        """
        )

        result = C.add(10, 20)

        response.result1 = result
    except Exception as e:
        response.result1 = str(e)

    return response.json()
