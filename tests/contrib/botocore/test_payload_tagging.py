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


@pytest.mark.subprocess(
    env={
        "DD_TRACE_CLOUD_REQUEST_PAYLOAD_TAGGING": None,
        "DD_TRACE_CLOUD_RESPONSE_PAYLOAD_TAGGING": None,
    }
)
def test_botocore_without_payload_tagging_does_not_import_jsonpath():
    import sys

    import botocore.session
    from botocore.stub import Stubber

    from ddtrace import config
    from ddtrace import patch

    patch(botocore=True)
    assert not config.botocore.payload_tagging_request
    assert not config.botocore.payload_tagging_response

    session = botocore.session.get_session()
    session.set_credentials(access_key="access-key", secret_key="secret-key")
    client = session.create_client("sns", region_name="us-east-1")
    with Stubber(client) as stubber:
        stubber.add_response("list_topics", {"Topics": [], "ResponseMetadata": {"HTTPStatusCode": 200}})
        assert client.list_topics()["Topics"] == []

    assert not any(
        name == "ddtrace.vendor.jsonpath" or name.startswith("ddtrace.vendor.jsonpath.") for name in sys.modules
    )


@pytest.mark.subprocess(
    parametrize={
        "DD_TRACE_CLOUD_REQUEST_PAYLOAD_TAGGING": ["", "all", "$..Custom"],
        "DD_TRACE_CLOUD_RESPONSE_PAYLOAD_TAGGING": ["", "all", "$..Custom"],
    }
)
def test_jsonpath_is_loaded_on_first_payload_redaction():
    import sys
    from types import SimpleNamespace

    from ddtrace import config
    from ddtrace._trace.span import Span
    from ddtrace._trace.utils_botocore import span_tags
    from ddtrace._trace.utils_botocore.aws_payload_tagging import _get_jsonpath_environment
    from ddtrace.contrib.internal.botocore.patch import patch

    patch()
    assert "ddtrace.vendor.jsonpath" not in sys.modules
    span = Span("sns.request")
    params = {"Attributes": {"Token": "secret"}, "Custom": "custom"}
    span_tags.set_botocore_patched_api_call_span_tags(
        span, SimpleNamespace(meta=SimpleNamespace(region_name="us-east-1")), ("Publish",), params, "sns", "Publish"
    )
    response_meta = {"HTTPStatusCode": 200, "Credentials": {"SecretAccessKey": "secret"}, "Custom": "custom"}
    span_tags.set_botocore_response_metadata_tags(span, {"ResponseMetadata": response_meta})

    request = config.botocore.payload_tagging_request
    response = config.botocore.payload_tagging_response
    assert ("ddtrace.vendor.jsonpath" in sys.modules) == bool(request or response)
    if request:
        assert span.get_tag("aws.request.body.Attributes.Token") == "redacted"
        assert span.get_tag("aws.request.body.Custom") == (
            "redacted" if "$..Custom" in (request, response) else "custom"
        )
    else:
        assert span.get_tag("aws.request.body.Attributes.Token") is None
    if response:
        assert span.get_tag("aws.response.body.Credentials.SecretAccessKey") == "redacted"
        assert span.get_tag("aws.response.body.Custom") == (
            "redacted" if "$..Custom" in (request, response) else "custom"
        )
    else:
        assert span.get_tag("aws.response.body.Credentials.SecretAccessKey") is None
    if request or response:
        assert _get_jsonpath_environment() is _get_jsonpath_environment()
    assert params["Attributes"]["Token"] == "secret"
    assert response_meta["Credentials"]["SecretAccessKey"] == "secret"
