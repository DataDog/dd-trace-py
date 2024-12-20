"""
cachetools==5.3.3

https://pypi.org/project/cachetools/
"""
from flask import Blueprint
from flask import jsonify
from flask import request

from .utils import ResultResponse


pkg_cachetools = Blueprint("package_cachetools", __name__)


@pkg_cachetools.route("/cachetools")
def pkg_cachetools_view():
    import cachetools

    response = ResultResponse(request.args.get("package_param"))

    try:
        param_value = request.args.get("package_param", "default-key")

        cache = cachetools.LRUCache(maxsize=2)

        @cachetools.cached(cache)
        def expensive_function(key):
            return f"Computed value for {key}"

        try:
            # Access the cache with the parameter value
            result_output = expensive_function(param_value)
            # Access the cache with another key to demonstrate LRU eviction
            expensive_function("another-key")
            # Access the cache with the parameter value again to show it is cached
            cached_value = expensive_function(param_value)
            result_output += f"\nCached value for {param_value}: {cached_value}"
        except Exception as e:
            result_output = f"Error: {str(e)}"

        response.result1 = result_output
    except Exception as e:
        response.result1 = f"Error: {str(e)}"

    return jsonify(response.json())


@pkg_cachetools.route("/cachetools_propagation")
def pkg_cachetools_propagation_view():
    import cachetools

    from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted

    response = ResultResponse(request.args.get("package_param"))

    try:
        param_value = request.args.get("package_param", "default-key")
        if not is_pyobject_tainted(param_value):
            response.result1 = "Error: package_param is not tainted"
            return jsonify(response.json())

        cache = cachetools.LRUCache(maxsize=2)

        @cachetools.cached(cache)
        def expensive_function(key):
            return f"Computed value for {key}"

        try:
            # Access the cache with the parameter value
            res = expensive_function(param_value)
            result_output = "OK" if is_pyobject_tainted(res) else f"Error: result is not tainted: {res}"
        except Exception as e:
            result_output = f"Error: {str(e)}"
    except Exception as e:
        result_output = f"Error: {str(e)}"

    response.result1 = result_output
    return jsonify(response.json())
