import copy

import pytest

from ddtrace._trace.utils_botocore.aws_payload_tagging import AWSPayloadTagging
from ddtrace._trace.utils_botocore.aws_payload_tagging import _compile_redaction_path


ALL_DEFAULTS = (
    AWSPayloadTagging._REDACTION_PATHS_DEFAULTS
    + AWSPayloadTagging._REQUEST_REDACTION_PATHS_DEFAULTS
    + AWSPayloadTagging._RESPONSE_REDACTION_PATHS_DEFAULTS
)


def _redact(payload, paths):
    data = copy.deepcopy(payload)
    AWSPayloadTagging()._redact_json(data, None, [_compile_redaction_path(p) for p in paths])
    return data


@pytest.mark.parametrize("path", ALL_DEFAULTS)
def test_default_redaction_paths_compile(path):
    assert _compile_redaction_path(path) is not None


@pytest.mark.parametrize(
    "path",
    [
        "$..Attr ibutes.PlatformCredential",
        "$..[",
        "$..)bad(",
        "$..foo[",
    ],
)
def test_invalid_paths_are_rejected(path):
    assert AWSPayloadTagging()._validate_json_paths(path) is False


@pytest.mark.parametrize("paths", ["all", "$..bucket", "$..bucket,$..HTTPHeaders.*"])
def test_valid_paths_are_accepted(paths):
    assert AWSPayloadTagging()._validate_json_paths(paths) is True


def test_dot_before_bracket_is_supported():
    # jsonpath-ng accepted this form, so it must keep working
    payload = {"PublishBatchRequestEntries": [{"Message": "m1"}, {"Message": "m2"}]}
    assert _redact(payload, ["$..PublishBatchRequestEntries.[*].Message"]) == {
        "PublishBatchRequestEntries": [{"Message": "redacted"}, {"Message": "redacted"}]
    }


def test_quoted_field_name_containing_dot_bracket():
    # a quoted field name may itself contain ".["
    payload = {"weird.[key]": "secret", "other": "keep"}
    assert _redact(payload, ["$['weird.[key]']"]) == {"weird.[key]": "redacted", "other": "keep"}


def test_redacts_list_elements():
    # regression: this used to raise AttributeError instead of redacting
    payload = {"phoneNumbers": ["+15550001", "+15550002"]}
    assert _redact(payload, ["$..phoneNumbers[*]"]) == {"phoneNumbers": ["redacted", "redacted"]}


def test_redacts_recursively_leaving_other_keys_intact():
    payload = {
        "Attributes": {"Token": "t1", "Keep": "keep"},
        "Nested": {"Attributes": {"Token": "t2"}},
    }
    assert _redact(payload, ["$..Attributes.Token"]) == {
        "Attributes": {"Token": "redacted", "Keep": "keep"},
        "Nested": {"Attributes": {"Token": "redacted"}},
    }


def test_redacts_wildcards_over_dicts_and_lists():
    payload = {
        "Endpoints": {"a": {"Token": "et1"}, "b": {"Token": "et2"}},
        "PhoneNumbers": [{"PhoneNumber": "p1"}, {"PhoneNumber": "p2"}],
    }
    assert _redact(payload, ["$..Endpoints.*.Token", "$..PhoneNumbers[*].PhoneNumber"]) == {
        "Endpoints": {"a": {"Token": "redacted"}, "b": {"Token": "redacted"}},
        "PhoneNumbers": [{"PhoneNumber": "redacted"}, {"PhoneNumber": "redacted"}],
    }


def test_missing_paths_leave_payload_unchanged():
    payload = {"Untouched": {"deep": "value"}}
    assert _redact(payload, ALL_DEFAULTS) == payload
