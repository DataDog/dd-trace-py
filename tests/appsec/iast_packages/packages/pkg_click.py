"""
click==8.1.3

https://pypi.org/project/click/
"""
from flask import Blueprint
from flask import request

from .utils import ResultResponse


pkg_click = Blueprint("package_click", __name__)


@pkg_click.route("/click")
def pkg_click_view():
    import click

    response = ResultResponse(request.args.get("package_param"))

    try:
        # Example usage of click package
        @click.command()
        @click.option("--count", default=1, help="Number of greetings.")
        @click.option("--name", prompt="Your name", help="The person to greet.")
        def hello(count, name):
            for _ in range(count):
                click.echo(f"Hello {name}!")

        # Simulate command line invocation
        from click.testing import CliRunner

        runner = CliRunner()
        result = runner.invoke(hello, ["--count", "2", "--name", "World"])
        response.result1 = result.output
    except Exception as e:
        response.result1 = f"Error: {str(e)}"
    return response.json()
