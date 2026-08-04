import copy

from ddtrace._trace.utils_botocore.aws_payload_tagging import AWSPayloadTagging
from ddtrace.vendor.jsonpath_ng.parser import JsonPathParser


def _redact(payload, paths):
    parser = JsonPathParser()
    data = copy.deepcopy(payload)
    AWSPayloadTagging()._redact_json(data, None, [parser.parse(p) for p in paths])
    return data


def test_redacts_list_elements():
    # regression: this used to raise AttributeError instead of redacting
    payload = {"phoneNumbers": ["+15550001", "+15550002"]}
    assert _redact(payload, ["$..phoneNumbers[*]"]) == {"phoneNumbers": ["redacted", "redacted"]}


def test_redacts_object_fields():
    payload = {"Attributes": {"Token": "t1", "Keep": "keep"}}
    assert _redact(payload, ["$..Attributes.Token"]) == {"Attributes": {"Token": "redacted", "Keep": "keep"}}


def test_redacts_fields_under_list_elements():
    payload = {"PhoneNumbers": [{"PhoneNumber": "p1"}, {"PhoneNumber": "p2"}]}
    assert _redact(payload, ["$..PhoneNumbers[*].PhoneNumber"]) == {
        "PhoneNumbers": [{"PhoneNumber": "redacted"}, {"PhoneNumber": "redacted"}]
    }
