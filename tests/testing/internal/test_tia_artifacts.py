import json
from unittest.mock import Mock
from unittest.mock import patch
from urllib.parse import parse_qs
from urllib.parse import urlsplit

import pytest

from ddtrace.testing.internal.api_client import APIClient
from ddtrace.testing.internal.constants import ITRSkippingLevel
from ddtrace.testing.internal.http import BackendConnector
from ddtrace.testing.internal.http import BackendResult
from ddtrace.testing.internal.telemetry import ErrorType
from ddtrace.testing.internal.tia.artifacts import ARTIFACTS_ENDPOINT
from ddtrace.testing.internal.tia.artifacts import MAX_ARTIFACT_BYTES
from ddtrace.testing.internal.tia.artifacts import Artifact
from ddtrace.testing.internal.tia.artifacts import ArtifactMetadata


HEAD = "a" * 40
SOURCE = "b" * 40
CONTENT = b"\x00\xffopaque database bytes\n"


@pytest.fixture
def metadata():
    return ArtifactMetadata(
        repository_id="repository/with spaces?&",
        suite_id="pytest tests/unit -m 'a & b'",
        env_hash="environment-hash",
        configurations={"os.platform": "Linux", "custom": "a&b= c/\u00e9"},
    )


@pytest.fixture
def client_and_connector(mock_telemetry):
    setup = Mock()
    connector = setup.get_connector_for_subdomain.return_value
    client = APIClient("tests", "ci", {}, ITRSkippingLevel.TEST, {}, setup, mock_telemetry)
    return client, connector


def lookup_result(content=CONTENT, matched=HEAD, source=SOURCE, status=200):
    headers = {"x-dd-tia-git-commit-sha": matched, "x-dd-tia-source-commit": source}
    response = Mock(status_code=status)
    response.header.side_effect = headers.get
    return BackendResult(response=response, response_body=content)


def test_upload_uses_put_raw_bytes_and_encoded_query(client_and_connector, metadata):
    client, connector = client_and_connector
    connector.request.return_value = BackendResult(
        response=Mock(status_code=201), parsed_response={"blob_id": "blob", "deduplicated": False}
    )
    assert client.store_tia_artifact(metadata, HEAD, CONTENT)
    args, kwargs = connector.request.call_args
    assert args[0] == "PUT"
    url = urlsplit(args[1])
    assert url.path == ARTIFACTS_ENDPOINT
    query = parse_qs(url.query)
    assert query == {
        "repository_id": [metadata.repository_id],
        "suite_id": [metadata.suite_id],
        "env_hash": [metadata.env_hash],
        "language": ["python"],
        "git_commit_sha": [HEAD],
        "compatibility_version": ["0"],
        "configurations": [json.dumps(metadata.configurations, sort_keys=True, separators=(",", ":"))],
    }
    assert kwargs["data"] is CONTENT
    assert kwargs["headers"] == {"Content-Type": "application/octet-stream"}
    assert kwargs["is_json_response"] is True
    assert not kwargs.get("send_gzip", False)


def test_lookup_keeps_matched_and_source_commits_separate(client_and_connector, metadata):
    client, connector = client_and_connector
    connector.request.return_value = lookup_result()
    assert client.lookup_tia_artifact(metadata, [HEAD]) == Artifact(CONTENT, HEAD, SOURCE)
    args, kwargs = connector.request.call_args
    assert args == ("POST", f"{ARTIFACTS_ENDPOINT}/lookup")
    payload = json.loads(kwargs["data"])
    assert payload == {
        "data": {
            "type": "test_params",
            "attributes": {
                **metadata.attributes(),
                "git_commit_shas": [HEAD],
            },
        }
    }
    assert kwargs["headers"] == {
        "Content-Type": "application/vnd.api+json",
        "Accept": "application/octet-stream",
    }
    assert not kwargs.get("is_json_response", False)


def test_lookup_caps_history_at_one_hundred_nearest_commits(client_and_connector, metadata):
    client, connector = client_and_connector
    commits = [f"{index:040x}" for index in range(101)]
    connector.request.return_value = lookup_result(matched=commits[99])
    assert client.lookup_tia_artifact(metadata, commits).matched_commit == commits[99]
    request = json.loads(connector.request.call_args.kwargs["data"])
    assert request["data"]["attributes"]["git_commit_shas"] == commits[:100]
    connector.request.return_value = lookup_result(matched=commits[100])
    assert client.lookup_tia_artifact(metadata, commits) is None


def test_lookup_404_is_a_cache_miss(client_and_connector, metadata):
    client, connector = client_and_connector
    result = lookup_result(status=404)
    result.error_type = ErrorType.CODE_4XX
    result.error_description = "404 Not Found"
    connector.request.return_value = result
    assert client.lookup_tia_artifact(metadata, [HEAD]) is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"matched": None},
        {"matched": "c" * 40},
        {"source": None},
        {"source": "--invalid"},
        {"content": None},
        {"content": b""},
        {"content": b"x" * (MAX_ARTIFACT_BYTES + 1)},
        {"status": 201},
    ],
)
def test_invalid_lookup_fails_open(client_and_connector, metadata, kwargs):
    client, connector = client_and_connector
    connector.request.return_value = lookup_result(**kwargs)
    assert client.lookup_tia_artifact(metadata, [HEAD]) is None


@pytest.mark.parametrize("size,expected", [(MAX_ARTIFACT_BYTES, True), (MAX_ARTIFACT_BYTES + 1, False)])
def test_upload_size_limit(client_and_connector, metadata, size, expected):
    client, connector = client_and_connector
    connector.request.return_value = BackendResult(response=Mock(status_code=201))
    assert client.store_tia_artifact(metadata, HEAD, b"x" * size) is expected
    assert connector.request.called is expected


@pytest.mark.parametrize("operation", ["store", "lookup"])
def test_transport_failure_does_not_raise(client_and_connector, metadata, operation):
    client, connector = client_and_connector
    connector.request.side_effect = TimeoutError("unavailable")
    if operation == "store":
        assert not client.store_tia_artifact(metadata, HEAD, CONTENT)
    else:
        assert client.lookup_tia_artifact(metadata, [HEAD]) is None


def test_optional_configurations_can_be_omitted():
    metadata = ArtifactMetadata("repository", "suite", "hash")
    assert "configurations" not in metadata.attributes()
    assert "configurations" not in parse_qs(urlsplit(metadata.upload_path(HEAD)).query)


def test_json_map_uses_source_header_as_diff_baseline(client_and_connector, metadata):
    client, connector = client_and_connector
    document = {"base_commit": HEAD, "map": {"version": 1, "files": []}}
    connector.request.return_value = lookup_result(content=json.dumps(document).encode())
    result = client.lookup_tia_map(metadata, [HEAD])
    assert result == {**document, "base_commit": SOURCE, "matched_commit": HEAD}


@pytest.mark.parametrize("content", [b"{", b"null", b"[]", b"\xff"])
def test_invalid_json_map_is_a_miss(client_and_connector, metadata, content):
    client, connector = client_and_connector
    connector.request.return_value = lookup_result(content=content)
    assert client.lookup_tia_map(metadata, [HEAD]) is None


def test_json_map_is_uploaded_as_raw_utf8_bytes(client_and_connector, metadata):
    client, connector = client_and_connector
    connector.request.return_value = BackendResult(
        response=Mock(status_code=201), parsed_response={"blob_id": "blob", "deduplicated": True}
    )
    document = {"base_commit": HEAD, "map": {"version": 1, "files": ["module.py"]}}
    assert client.store_tia_map(metadata, document)
    content = connector.request.call_args.kwargs["data"]
    assert isinstance(content, bytes)
    assert json.loads(content) == document
    query = parse_qs(urlsplit(connector.request.call_args.args[1]).query)
    assert query["git_commit_sha"] == [HEAD]


def test_lookup_miss_uses_debug_logging_and_agentless_api_key():
    response = Mock(status_code=404)
    response.header.return_value = None
    response.body.return_value = b""
    with (
        patch("ddtrace.testing.internal.http.HTTPClient") as http_client,
        patch("ddtrace.testing.internal.http.log") as logger,
    ):
        http_client.return_value.post.return_value = response
        connector = BackendConnector("https://api.datad0g.com", default_headers={"dd-api-key": "test-key"})
        result = connector.request("POST", f"{ARTIFACTS_ENDPOINT}/lookup", data=b"{}", expected_status_codes=(404,))
    assert result.error_type == ErrorType.CODE_4XX
    logger.warning.assert_not_called()
    logger.debug.assert_called()
    assert dict(http_client.return_value.post.call_args.kwargs["headers"])["dd-api-key"] == "test-key"
