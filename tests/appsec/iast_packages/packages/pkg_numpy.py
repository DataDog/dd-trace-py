"""
numpy==1.26.2

https://pypi.org/project/numpy/
"""
from flask import Blueprint
from flask import request
import numpy as np

from .utils import ResultResponse


pkg_numpy = Blueprint("package_numpy", __name__)


def np_float(x):
    return float(x)


@pkg_numpy.route("/numpy")
def pkg_idna_view():
    response = ResultResponse(request.args.get("package_param"))
    res = np.array(response.package_param.split(" "))
    vfunc = np.vectorize(np_float)
    res2 = vfunc(res)
    response.result1 = np.sort(res2).tolist()
    response.result2 = float(response.result1[2])
    return response.json()
