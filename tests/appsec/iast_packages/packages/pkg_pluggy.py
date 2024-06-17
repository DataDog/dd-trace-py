"""
pluggy==1.5.0

https://pypi.org/project/pluggy/
"""
from flask import Blueprint
from flask import request

from .utils import ResultResponse


pkg_pluggy = Blueprint("package_pluggy", __name__)


@pkg_pluggy.route("/pluggy")
def pkg_pluggy_view():
    import pluggy

    response = ResultResponse(request.args.get("package_param"))

    try:
        param_value = request.args.get("package_param", "default-hook")

        hook_spec = pluggy.HookspecMarker("example")

        class PluginSpec:
            @hook_spec
            def myhook(self, arg):
                pass

        hook_impl = pluggy.HookimplMarker("example")

        class PluginImpl:
            @hook_impl
            def myhook(self, arg):
                return f"Plugin received: {arg}"

        pm = pluggy.PluginManager("example")
        pm.add_hookspecs(PluginSpec)
        pm.register(PluginImpl())

        result_output = pm.hook.myhook(arg=param_value)

        response.result1 = f"Hook result: {result_output[0]}"
    except Exception as e:
        response.result1 = f"Error: {str(e)}"

    return response.json()
