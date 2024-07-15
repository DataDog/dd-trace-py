"""
jsonschema==4.22.0
https://pypi.org/project/jsonschema/
"""
from flask import Blueprint
from flask import request

from .utils import ResultResponse


pkg_jsonschema = Blueprint("package_jsonschema", __name__)


@pkg_jsonschema.route("/jsonschema")
def pkg_jsonschema_view():
    import jsonschema
    from jsonschema import validate

    response = ResultResponse(request.args.get("package_param"))

    try:
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}, "age": {"type": "number"}},
            "required": ["name", "age"],
        }

        data = {"name": response.package_param, "age": 65}

        validate(instance=data, schema=schema)

        response.result1 = {"schema": schema, "data": data, "validation": "successful"}
    except jsonschema.exceptions.ValidationError as e:
        response.result1 = f"Validation error: {e.message}"
    except Exception as e:
        response.result1 = str(e)

    return response.json()
