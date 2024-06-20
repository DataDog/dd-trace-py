"""
pydantic==2.7.1

https://pypi.org/project/pydantic/
"""
from flask import Blueprint
from flask import request

from .utils import ResultResponse


pkg_pydantic = Blueprint("package_pydantic", __name__)


@pkg_pydantic.route("/pydantic")
def pkg_pydantic_view():
    from pydantic import BaseModel
    from pydantic import ValidationError

    class Item(BaseModel):
        name: str
        description: str = None

    response = ResultResponse(request.args.get("package_param"))

    try:
        param_value = request.args.get("package_param", '{"name": "default-item"}')

        try:
            item = Item.model_validate_json(param_value)
            result_output = f"Validated item: name={item.name}, description={item.description}"
        except ValidationError as e:
            result_output = f"Validation error: {str(e)}"

        response.result1 = result_output
    except Exception as e:
        response.result1 = f"Error: {str(e)}"

    return response.json()
