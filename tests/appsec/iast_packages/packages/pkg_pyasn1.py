"""
pyasn1==0.6.0
https://pypi.org/project/pyasn1/
"""

from flask import Blueprint
from flask import request

from .utils import ResultResponse


pkg_pyasn1 = Blueprint("package_pyasn1", __name__)


@pkg_pyasn1.route("/pyasn1")
def pkg_pyasn1_view():
    from pyasn1.codec.der import decoder
    from pyasn1.codec.der import encoder
    from pyasn1.type import namedtype
    from pyasn1.type import univ

    response = ResultResponse(request.args.get("package_param"))

    try:

        class ExampleASN1Structure(univ.Sequence):
            componentType = namedtype.NamedTypes(
                namedtype.NamedType("name", univ.OctetString()), namedtype.NamedType("age", univ.Integer())
            )

        example = ExampleASN1Structure()
        example.setComponentByName("name", response.package_param)
        example.setComponentByName("age", 65)

        encoded_data = encoder.encode(example)

        decoded_data, _ = decoder.decode(encoded_data, asn1Spec=ExampleASN1Structure())

        response.result1 = {
            "decoded_name": str(decoded_data.getComponentByName("name")),
            "decoded_age": int(decoded_data.getComponentByName("age")),
        }
    except Exception as e:
        response.result1 = str(e)

    return response.json()


@pkg_pyasn1.route("/pyasn1_propagation")
def pkg_pyasn1_propagation_view():
    from pyasn1.codec.der import decoder
    from pyasn1.codec.der import encoder
    from pyasn1.type import namedtype
    from pyasn1.type import univ

    from ddtrace.appsec._iast._taint_tracking._taint_objects_base import is_pyobject_tainted

    response = ResultResponse(request.args.get("package_param"))
    if not is_pyobject_tainted(response.package_param):
        response.result1 = "Error: package_param is not tainted"
        return response.json()

    try:

        class ExampleASN1StructurePropagation(univ.Sequence):
            componentType = namedtype.NamedTypes(
                namedtype.NamedType("name", univ.OctetString()), namedtype.NamedType("age", univ.Integer())
            )

        example = ExampleASN1StructurePropagation()
        example.setComponentByName("name", response.package_param)
        example.setComponentByName("age", 65)
        encoded_data = encoder.encode(example)
        decoded_data, _ = decoder.decode(encoded_data, asn1Spec=ExampleASN1StructurePropagation())
        res = decoded_data.getComponentByName("name")
        response.result1 = "OK" if is_pyobject_tainted(res) else "Error: res is not tainted: %s" % res
    except Exception as e:
        response.result1 = str(e)

    return response.json()
