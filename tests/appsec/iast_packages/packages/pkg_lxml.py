"""
lxml==5.2.2

https://pypi.org/project/lxml/
"""
from flask import Blueprint
from flask import request

from .utils import ResultResponse


pkg_lxml = Blueprint("package_lxml", __name__)


@pkg_lxml.route("/lxml")
def pkg_lxml_view():
    from lxml import etree

    response = ResultResponse(request.args.get("package_param"))

    try:
        xml_string = request.args.get("package_param", "<root><element>default-value</element></root>")

        try:
            root = etree.fromstring(xml_string)
            element = root.find("element")
            result_output = f"Element text: {element.text}" if element is not None else "No element found."
        except etree.XMLSyntaxError as e:
            result_output = f"Invalid XML: {str(e)}"

        response.result1 = result_output
    except Exception as e:
        response.result1 = f"Error: {str(e)}"

    return response.json()
