"""
aiohttp==3.9.5

https://pypi.org/project/aiohttp/
"""
from flask import Blueprint
from flask import jsonify
from flask import request

from .utils import ResultResponse


pkg_aiohttp = Blueprint("package_aiohttp", __name__)


@pkg_aiohttp.route("/aiohttp")
def pkg_aiohttp_view():
    import asyncio

    response = ResultResponse(request.args.get("package_param"))

    async def fetch(url):
        import aiohttp

        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                return await resp.text()

    try:
        url = request.args.get("package_param", "https://example.com")

        try:
            # Use asyncio to run the async function
            result_output = asyncio.run(fetch(url))
        except Exception as e:
            result_output = f"Error: {str(e)}"

        response.result1 = result_output
    except Exception as e:
        response.result1 = f"Error: {str(e)}"

    return jsonify(response.json())
