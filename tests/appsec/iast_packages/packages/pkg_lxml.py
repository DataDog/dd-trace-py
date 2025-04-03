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


@pkg_lxml.route("/lxml_propagation")
def pkg_lxml_propagation_view():
    from lxml import etree

    from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted

    response = ResultResponse(request.args.get("package_param"))
    if not is_pyobject_tainted(response.package_param):
        response.result1 = "Error: package_param is not tainted"
        return response.json()

    try:
        xml_string = request.args.get("package_param", "<root><element>default-value</element></root>")

        try:
            root = etree.fromstring(xml_string)
            element = root.find("element")
            response.result1 = (
                "OK" if is_pyobject_tainted(element.text) else "Error: element is not tainted: %s" % element.text
            )
        except etree.XMLSyntaxError as e:
            response.result1 = f"Invalid XML: {str(e)}"

    except Exception as e:
        response.result1 = f"Error: {str(e)}"

    return response.json()
