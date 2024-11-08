import copy
import json
import os

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import Source as RangeSource
from ddtrace.appsec._iast._taint_tracking import TaintRange
from ddtrace.appsec._iast._taint_tracking import new_pyobject_id
from ddtrace.appsec._iast._taint_tracking import set_ranges


ROOT_DIR = os.path.dirname(os.path.abspath(__file__))


def get_parametrize(vuln_type, ignore_list=None):
    fixtures_filename = os.path.join(ROOT_DIR, "redaction_fixtures", "evidence-redaction-suite.json")
    data = json.loads(open(fixtures_filename).read())
    idx = -1
    for element in data["suite"]:
        if element["type"] == "VULNERABILITIES":
            evidence_parameters = [
                param for k, params in element.get("parameters", {}).items() for param in params if param == vuln_type
            ]
            if evidence_parameters:
                evidence_input = [ev["evidence"] for ev in element["input"]]
            else:
                evidence_input = [ev["evidence"] for ev in element["input"] if ev["type"] == vuln_type]

            if evidence_input:
                sources_expected = element["expected"]["sources"][0]
                vulnerabilities_expected = element["expected"]["vulnerabilities"][0]
                parameters = element.get("parameters", [])
                if parameters:
                    for replace, values in parameters.items():
                        for value in values:
                            idx += 1
                            if ignore_list and idx in ignore_list:
                                continue

                            evidence_input_copy = {}
                            if evidence_input:
                                evidence_input_copy = copy.deepcopy(evidence_input[0])
                                evidence_input_copy["value"] = evidence_input_copy["value"].replace(replace, value)
                            vulnerabilities_expected_copy = copy.deepcopy(vulnerabilities_expected)
                            for value_part in vulnerabilities_expected_copy["evidence"]["valueParts"]:
                                if value_part.get("value"):
                                    value_part["value"] = value_part["value"].replace(replace, value)

                            yield evidence_input_copy, sources_expected, vulnerabilities_expected_copy
                else:
                    idx += 1
                    if ignore_list and idx in ignore_list:
                        continue

                    yield evidence_input[0], sources_expected, vulnerabilities_expected


def _taint_pyobject_multiranges(pyobject, elements):
    pyobject_ranges = []

    pyobject_newid = new_pyobject_id(pyobject)

    for element in elements:
        source_name, source_value, source_origin, start, len_range = element
        if isinstance(source_name, (bytes, bytearray)):
            source_name = str(source_name, encoding="utf8")
        if isinstance(source_value, (bytes, bytearray)):
            source_value = str(source_value, encoding="utf8")
        if source_origin is None:
            source_origin = OriginType.PARAMETER
        source = RangeSource(source_name, source_value, source_origin)
        pyobject_range = TaintRange(start, len_range, source)
        pyobject_ranges.append(pyobject_range)

    set_ranges(pyobject_newid, pyobject_ranges)
    return pyobject_newid
