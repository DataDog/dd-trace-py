"""
aiosignal==1.2.0

https://pypi.org/project/aiosignal/
"""
import asyncio

from flask import Blueprint
from flask import jsonify
from flask import request

from .utils import ResultResponse


pkg_aiosignal = Blueprint("package_aiosignal", __name__)


@pkg_aiosignal.route("/aiosignal")
def pkg_aiosignal_view():
    from aiosignal import Signal

    response = ResultResponse(request.args.get("package_param"))

    async def handler_1(sender, **kwargs):
        return "Handler 1 called"

    async def handler_2(sender, **kwargs):
        return "Handler 2 called"

    try:
        param_value = request.args.get("package_param", "default_value")

        try:
            signal = Signal(owner=None)
            signal.append(handler_1)
            signal.append(handler_2)
            signal.freeze()  # Freeze the signal to allow sending

            async def emit_signal():
                results = await signal.send(param_value)
                return results

            # Use asyncio to run the async function and gather results
            results = asyncio.run(emit_signal())
            result_output = f"Signal handlers results: {results}"
        except Exception as e:
            result_output = f"Error: {str(e)}"

        response.result1 = result_output
    except Exception as e:
        response.result1 = f"Error: {str(e)}"

    return jsonify(response.json())
