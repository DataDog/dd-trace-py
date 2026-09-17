import gzip
import json

import pytest

from ddtrace.internal.openfeature._agentless import DEFAULT_AGENTLESS_PATH
from ddtrace.internal.openfeature._agentless import build_agentless_endpoint
from ddtrace.internal.openfeature._agentless import decode_response_body
from ddtrace.internal.openfeature._agentless import parse_ufc_configuration


# ---------------------------------------------------------------------------
# Endpoint derivation
# ---------------------------------------------------------------------------


def test_endpoint_managed_default_site():
    assert build_agentless_endpoint("datadoghq.com") == (
        "https://ufc-server.ff-cdn.datadoghq.com" + DEFAULT_AGENTLESS_PATH
    )


def test_endpoint_site_is_lowercased():
    assert build_agentless_endpoint("DataDogHQ.com") == (
        "https://ufc-server.ff-cdn.datadoghq.com" + DEFAULT_AGENTLESS_PATH
    )


def test_endpoint_managed_staging_site():
    assert build_agentless_endpoint("datad0g.com") == ("https://ufc-server.ff-cdn.datad0g.com" + DEFAULT_AGENTLESS_PATH)


def test_endpoint_managed_govcloud_site():
    assert build_agentless_endpoint("ddog-gov.com") == (
        "https://ufc-server.ff-cdn.ddog-gov.com" + DEFAULT_AGENTLESS_PATH
    )


def test_endpoint_dd_env_added_when_set():
    url = build_agentless_endpoint("datadoghq.com", env="prod")
    assert url == "https://ufc-server.ff-cdn.datadoghq.com" + DEFAULT_AGENTLESS_PATH + "?dd_env=prod"


def test_endpoint_dd_env_omitted_when_unset():
    assert "dd_env" not in build_agentless_endpoint("datadoghq.com", env=None)
    assert "dd_env" not in build_agentless_endpoint("datadoghq.com", env="")


def test_endpoint_dd_env_is_url_encoded():
    url = build_agentless_endpoint("datadoghq.com", env="my env/1")
    assert "dd_env=my+env%2F1" in url


def test_endpoint_custom_origin_receives_standard_path():
    assert build_agentless_endpoint("datadoghq.com", base_url="https://flags.dev.internal:8080") == (
        "https://flags.dev.internal:8080" + DEFAULT_AGENTLESS_PATH
    )


def test_endpoint_custom_root_path_receives_standard_path():
    assert build_agentless_endpoint("datadoghq.com", base_url="https://flags.dev.internal/") == (
        "https://flags.dev.internal" + DEFAULT_AGENTLESS_PATH
    )


def test_endpoint_custom_non_root_path_used_verbatim():
    assert (
        build_agentless_endpoint("datadoghq.com", base_url="https://example.com/custom/ufc?tenant=one")
        == "https://example.com/custom/ufc?tenant=one"
    )


def test_endpoint_custom_http_allowed_any_host():
    # #9481 removed the loopback-only guard: an explicit custom endpoint is
    # operator-owned trust and may be cleartext on any host.
    assert build_agentless_endpoint("datadoghq.com", base_url="http://host.docker.internal:8126") == (
        "http://host.docker.internal:8126" + DEFAULT_AGENTLESS_PATH
    )


def test_endpoint_custom_base_url_is_trimmed():
    assert build_agentless_endpoint("datadoghq.com", base_url="  https://x.test/ufc  ") == "https://x.test/ufc"


def test_endpoint_rejects_non_http_scheme():
    with pytest.raises(ValueError):
        build_agentless_endpoint("datadoghq.com", base_url="ftp://flags.dev.internal")


def test_endpoint_rejects_malformed_url_without_leaking_value():
    sentinel = "sensitive-value"
    with pytest.raises(ValueError) as excinfo:
        build_agentless_endpoint("datadoghq.com", base_url="https://%s value" % sentinel)
    assert sentinel not in str(excinfo.value)


# ---------------------------------------------------------------------------
# gzip decoding
# ---------------------------------------------------------------------------


def test_decode_gzip_body():
    raw = b'{"hello": "world"}'
    assert decode_response_body(gzip.compress(raw), "gzip") == raw


def test_decode_gzip_case_insensitive():
    raw = b"payload"
    assert decode_response_body(gzip.compress(raw), "GZIP") == raw


def test_decode_passthrough_when_not_gzip():
    raw = b"plain"
    assert decode_response_body(raw, None) == raw
    assert decode_response_body(raw, "identity") == raw


def test_decode_raises_on_bad_gzip():
    with pytest.raises((OSError, EOFError)):
        decode_response_body(b"not gzip", "gzip")


# ---------------------------------------------------------------------------
# JSON:API validation
# ---------------------------------------------------------------------------


def _valid_envelope(**attr_overrides):
    attributes = {
        "format": "SERVER",
        "createdAt": "2024-01-01T00:00:00Z",
        "environment": {"name": "production"},
        "flags": {"my-flag": {"enabled": True}},
    }
    attributes.update(attr_overrides)
    return {"data": {"id": "1", "type": "universal-flag-configuration", "attributes": attributes}}


def test_parse_accepts_valid_and_returns_attributes_only():
    envelope = _valid_envelope()
    attributes = parse_ufc_configuration(json.dumps(envelope))
    assert attributes == envelope["data"]["attributes"]
    assert "data" not in attributes


def test_parse_accepts_bytes_body():
    attributes = parse_ufc_configuration(json.dumps(_valid_envelope()).encode("utf-8"))
    assert attributes["environment"]["name"] == "production"


def test_parse_accepts_empty_flags_object():
    attributes = parse_ufc_configuration(json.dumps(_valid_envelope(flags={})))
    assert attributes["flags"] == {}


@pytest.mark.parametrize(
    "body",
    [
        "not json at all",
        "",
        json.dumps([1, 2, 3]),  # top-level not an object
        json.dumps({"data": None}),
        json.dumps({"data": {"type": "something-else", "attributes": {}}}),
        json.dumps({"data": {"type": "universal-flag-configuration"}}),  # missing attributes
    ],
)
def test_parse_rejects_bad_envelope(body):
    with pytest.raises(ValueError):
        parse_ufc_configuration(body)


@pytest.mark.parametrize(
    "attr_overrides",
    [
        {"format": 123},  # non-string format
        {"createdAt": None},  # non-string createdAt
        {"environment": {}},  # missing environment.name
        {"environment": "production"},  # environment not an object
        {"flags": []},  # flags is an array, not an object
        {"flags": None},  # flags missing
    ],
)
def test_parse_rejects_bad_attributes(attr_overrides):
    with pytest.raises(ValueError):
        parse_ufc_configuration(json.dumps(_valid_envelope(**attr_overrides)))
