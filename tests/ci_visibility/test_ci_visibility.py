import contextlib
from ctypes import c_int
import json
import multiprocessing
import re
import socket
import textwrap
import time
from typing import Set
from unittest.mock import Mock

import mock
import msgpack
import pytest

import ddtrace
from ddtrace.constants import AUTO_KEEP
from ddtrace.ext import ci
from ddtrace.ext.git import _build_git_packfiles_with_details
from ddtrace.ext.git import _GitSubprocessDetails
import ddtrace.ext.test_visibility.api as ext_api
from ddtrace.internal.ci_visibility import CIVisibility
from ddtrace.internal.ci_visibility._api_client import ITRData
from ddtrace.internal.ci_visibility._api_client import TestVisibilityAPISettings
from ddtrace.internal.ci_visibility.constants import EVP_PROXY_AGENT_BASE_PATH
from ddtrace.internal.ci_visibility.constants import REQUESTS_MODE
from ddtrace.internal.ci_visibility.encoder import CIVisibilityEncoderV01
from ddtrace.internal.ci_visibility.filters import TraceCiVisibilityFilter
from ddtrace.internal.ci_visibility.git_client import METADATA_UPLOAD_STATUS
from ddtrace.internal.ci_visibility.git_client import CIVisibilityGitClient
from ddtrace.internal.ci_visibility.git_client import CIVisibilityGitClientSerializerV1
from ddtrace.internal.ci_visibility.recorder import CIVisibilityTracer
from ddtrace.internal.ci_visibility.recorder import _extract_repository_name_from_url
import ddtrace.internal.test_visibility._internal_item_ids
from ddtrace.internal.test_visibility._library_capabilities import LibraryCapabilities
from ddtrace.internal.utils.http import Response
from ddtrace.settings._config import Config
from ddtrace.trace import Span
from tests.ci_visibility.api_client._util import _make_fqdn_suite_ids
from tests.ci_visibility.api_client._util import _make_fqdn_test_ids
from tests.ci_visibility.util import _ci_override_env
from tests.ci_visibility.util import _get_default_civisibility_ddconfig
from tests.ci_visibility.util import _patch_dummy_writer
from tests.ci_visibility.util import set_up_mock_civisibility
from tests.utils import DummyCIVisibilityWriter
from tests.utils import DummyTracer
from tests.utils import TracerTestCase
from tests.utils import override_global_config


TEST_SHA_1 = "b3672ea5cbc584124728c48a443825d2940e0ddd"
TEST_SHA_2 = "b3672ea5cbc584124728c48a443825d2940e0eee"


@pytest.fixture(scope="function", autouse=True)
def _disable_ci_visibility():
    try:
        if CIVisibility.enabled:
            CIVisibility.disable()
    except Exception:  # noqa: E722
        # no-dd-sa:python-best-practices/no-silent-exception
        pass
    yield
    try:
        if CIVisibility.enabled:
            CIVisibility.disable()
    except Exception:  # noqa: E722
        # no-dd-sa:python-best-practices/no-silent-exception
        pass


@contextlib.contextmanager
def _dummy_noop_git_client():
    with mock.patch.multiple(
        CIVisibilityGitClient,
        _get_repository_url=mock.Mock(return_value="https://testrepo.url:1245/reporepo.git"),
        _is_shallow_repository=mock.Mock(return_value=True),
        _get_latest_commits=mock.Mock(return_value=["latest1", "latest2"]),
        _search_commits=mock.Mock(return_value=["latest1", "searched1", "searched2"]),
        _get_filtered_revisions=mock.Mock(return_value=""),
        upload_git_metadata=mock.Mock(return_value=None),
        metadata_upload_finished=mock.Mock(return_value=True),
        wait_for_metadata_upload_status=mock.Mock(return_value=METADATA_UPLOAD_STATUS.SUCCESS),
    ):
        yield


def test_filters_test_spans():
    trace_filter = TraceCiVisibilityFilter(tags={"hello": "world"}, service="test-service")
    root_test_span = Span(name="span1", span_type="test")
    root_test_span._local_root = root_test_span
    # Root span in trace is a test
    trace = [root_test_span]
    assert trace_filter.process_trace(trace) == trace
    assert root_test_span.get_tag(ci.LIBRARY_VERSION) == ddtrace.__version__
    assert root_test_span.get_tag("hello") == "world"
    assert root_test_span.context.dd_origin == ci.CI_APP_TEST_ORIGIN
    assert root_test_span.context.sampling_priority == AUTO_KEEP


def test_filters_non_test_spans():
    trace_filter = TraceCiVisibilityFilter(tags={"hello": "world"}, service="test-service")
    root_span = Span(name="span1")
    root_span._local_root = root_span
    # Root span in trace is not a test
    trace = [root_span]
    assert trace_filter.process_trace(trace) is None


def test_ci_visibility_service_enable():
    with _ci_override_env(
        dict(
            DD_API_KEY="foobar.baz",
            DD_CIVISIBILITY_AGENTLESS_ENABLED="1",
        )
    ), _dummy_noop_git_client(), mock.patch(
        "ddtrace.internal.ci_visibility._api_client._TestVisibilityAPIClientBase.fetch_settings",
        return_value=TestVisibilityAPISettings(False, False, False, False),
    ):
        with _patch_dummy_writer():
            dummy_tracer = DummyTracer()
            CIVisibility.enable(tracer=dummy_tracer, service="test-service")
            ci_visibility_instance = CIVisibility._instance
            assert ci_visibility_instance is not None
            assert CIVisibility.enabled
            assert ci_visibility_instance.tracer == dummy_tracer
            assert ci_visibility_instance._service == "test-service"
            assert ci_visibility_instance._api_settings.coverage_enabled is False
            assert ci_visibility_instance._api_settings.skipping_enabled is False
            assert any(
                isinstance(tracer_filter, TraceCiVisibilityFilter)
                for tracer_filter in dummy_tracer._user_trace_processors
            )
            CIVisibility.disable()


def test_ci_visibility_service_enable_without_service():
    """Test that enabling works and sets the right service when service isn't provided as a parameter to enable()"""
    with _ci_override_env(
        dict(
            DD_API_KEY="foobar.baz",
            DD_CIVISIBILITY_AGENTLESS_ENABLED="1",
        )
    ), _dummy_noop_git_client(), mock.patch(
        "ddtrace.internal.ci_visibility._api_client._TestVisibilityAPIClientBase.fetch_settings",
        return_value=TestVisibilityAPISettings(False, False, False, False),
    ), mock.patch(
        "ddtrace.internal.ci_visibility.recorder._extract_repository_name_from_url", return_value="test-repo"
    ):
        with _patch_dummy_writer():
            dummy_tracer = DummyTracer()
            CIVisibility.enable(tracer=dummy_tracer)
            ci_visibility_instance = CIVisibility._instance
            assert ci_visibility_instance is not None
            assert CIVisibility.enabled
            assert ci_visibility_instance.tracer == dummy_tracer
            assert ci_visibility_instance._service == "test-repo"  # Inherited from environment
            assert ci_visibility_instance._api_settings.coverage_enabled is False
            assert ci_visibility_instance._api_settings.skipping_enabled is False
            assert any(
                isinstance(tracer_filter, TraceCiVisibilityFilter)
                for tracer_filter in dummy_tracer._user_trace_processors
            )
            CIVisibility.disable()


@mock.patch("ddtrace.internal.ci_visibility._api_client._TestVisibilityAPIClientBase._do_request")
def test_ci_visibility_service_enable_with_app_key_and_itr_disabled(_do_request):
    with _ci_override_env(
        dict(
            DD_API_KEY="foobar.baz",
            DD_APP_KEY="foobar",
            DD_CIVISIBILITY_AGENTLESS_ENABLED="1",
            DD_CIVISIBILITY_ITR_ENABLED="0",
        )
    ), _dummy_noop_git_client(), mock.patch(
        "ddtrace.internal.ci_visibility.recorder.ddconfig", _get_default_civisibility_ddconfig()
    ):
        with _patch_dummy_writer():
            _do_request.return_value = Response(
                status=200,
                body='{"data":{"id":"1234","type":"ci_app_tracers_test_service_settings","attributes":'
                '{"code_coverage":true,"tests_skipping":true}}}',
            )
            dummy_tracer = DummyTracer()
            CIVisibility.enable(tracer=dummy_tracer, service="test-service")
            assert CIVisibility._instance._api_settings.coverage_enabled is False
            assert CIVisibility._instance._api_settings.skipping_enabled is False
            CIVisibility.disable()


@mock.patch(
    "ddtrace.internal.ci_visibility._api_client._TestVisibilityAPIClientBase._do_request", side_effect=TimeoutError
)
def test_ci_visibility_service_settings_timeout(_do_request):
    with _ci_override_env(
        dict(
            DD_API_KEY="foobar.baz",
            DD_APP_KEY="foobar",
            DD_CIVISIBILITY_AGENTLESS_ENABLED="1",
        )
    ), _dummy_noop_git_client(), mock.patch(
        "ddtrace.internal.ci_visibility.recorder.ddconfig", _get_default_civisibility_ddconfig()
    ):
        CIVisibility.enable(service="test-service")
        assert CIVisibility._instance._api_settings.coverage_enabled is False
        assert CIVisibility._instance._api_settings.skipping_enabled is False
        CIVisibility.disable()


@mock.patch(
    "ddtrace.internal.ci_visibility._api_client._TestVisibilityAPIClientBase._do_request", side_effect=socket.timeout
)
def test_ci_visibility_service_settings_socket_timeout(_do_request):
    with _ci_override_env(
        dict(
            DD_API_KEY="foobar.baz",
            DD_APP_KEY="foobar",
            DD_CIVISIBILITY_AGENTLESS_ENABLED="1",
        )
    ), _dummy_noop_git_client(), mock.patch(
        "ddtrace.internal.ci_visibility.recorder.ddconfig", _get_default_civisibility_ddconfig()
    ):
        CIVisibility.enable(service="test-service")
        assert CIVisibility._instance._api_settings.coverage_enabled is False
        assert CIVisibility._instance._api_settings.skipping_enabled is False
        CIVisibility.disable()


@mock.patch(
    "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
    return_value=TestVisibilityAPISettings(True, True, False, True),
)
@mock.patch(
    "ddtrace.internal.ci_visibility._api_client._TestVisibilityAPIClientBase._do_request", side_effect=TimeoutError
)
def test_ci_visibility_service_skippable_timeout(_do_request, _check_enabled_features):
    with _ci_override_env(
        dict(
            DD_API_KEY="foobar.baz",
            DD_APP_KEY="foobar",
            DD_CIVISIBILITY_AGENTLESS_ENABLED="1",
        )
    ), _dummy_noop_git_client(), mock.patch(
        "ddtrace.internal.ci_visibility.recorder.ddconfig", _get_default_civisibility_ddconfig()
    ):
        CIVisibility.enable(service="test-service")
        assert CIVisibility._instance._itr_data is None
        CIVisibility.disable()


@mock.patch(
    "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
    return_value=TestVisibilityAPISettings(True, True, False, True),
)
@mock.patch(
    "ddtrace.internal.ci_visibility._api_client._TestVisibilityAPIClientBase._do_request", side_effect=ValueError
)
def test_ci_visibility_service_skippable_other_error(_do_request, _check_enabled_features):
    with _ci_override_env(
        dict(
            DD_API_KEY="foobar.baz",
            DD_APP_KEY="foobar",
            DD_CIVISIBILITY_AGENTLESS_ENABLED="1",
        )
    ), _dummy_noop_git_client(), mock.patch(
        "ddtrace.internal.ci_visibility.recorder.ddconfig", _get_default_civisibility_ddconfig()
    ):
        CIVisibility.enable(service="test-service")
        assert CIVisibility._instance._itr_data is None
        CIVisibility.disable()


@mock.patch("ddtrace.internal.ci_visibility._api_client._TestVisibilityAPIClientBase._do_request")
def test_ci_visibility_service_enable_with_itr_enabled(_do_request):
    with _ci_override_env(
        dict(
            DD_API_KEY="foobar.baz",
            DD_CIVISIBILITY_AGENTLESS_ENABLED="1",
        )
    ), _dummy_noop_git_client(), mock.patch(
        "ddtrace.internal.ci_visibility.recorder.CIVisibility._fetch_tests_to_skip"
    ), mock.patch(
        "ddtrace.internal.ci_visibility.recorder.ddconfig", _get_default_civisibility_ddconfig()
    ):
        _do_request.return_value = Response(
            status=200,
            body=textwrap.dedent(
                """
            {
                "data": {
                    "id": "d14a9b0b-c83c-4eb9-ad3c-d7115c634b1a",
                    "type": "ci_app_tracers_test_service_settings",
                    "attributes": {
                        "code_coverage": true,
                        "early_flake_detection": {
                            "enabled": false,
                            "slow_test_retries": {
                                "10s": 5,
                                "30s": 3,
                                "5m": 2,
                                "5s": 10,
                                "faulty_session_threshold": 30
                            }
                        },
                        "known_tests_enabled": false,
                        "flaky_test_retries_enabled": false,
                        "itr_enabled": true,
                        "require_git": false,
                        "tests_skipping": true
                    }
                }
            }
            """
            ),
        )
        CIVisibility.enable(service="test-service")
        assert CIVisibility._instance._api_settings.coverage_enabled is True
        assert CIVisibility._instance._api_settings.skipping_enabled is True
        CIVisibility.disable()


@mock.patch("ddtrace.internal.ci_visibility._api_client._TestVisibilityAPIClientBase._do_request")
@pytest.mark.parametrize("agentless_enabled", [False, True])
def test_ci_visibility_service_enable_with_itr_disabled_in_env(_do_request, agentless_enabled):
    agentless_enabled_str = "1" if agentless_enabled else "0"
    with _ci_override_env(
        dict(
            DD_API_KEY="foobar.baz",
            DD_CIVISIBILITY_AGENTLESS_ENABLED=agentless_enabled_str,
            DD_CIVISIBILITY_ITR_ENABLED="0",
        )
    ), _dummy_noop_git_client(), mock.patch(
        "ddtrace.internal.ci_visibility.recorder.ddconfig", _get_default_civisibility_ddconfig()
    ):
        CIVisibility.enable(service="test-service")
        assert CIVisibility._instance._api_settings.coverage_enabled is False
        assert CIVisibility._instance._api_settings.skipping_enabled is False
        if agentless_enabled:
            _do_request.assert_called()
        CIVisibility.disable()


@mock.patch("ddtrace.internal.ci_visibility._api_client._TestVisibilityAPIClientBase._do_request")
def test_ci_visibility_service_enable_with_app_key_and_error_response(_do_request):
    with _ci_override_env(
        dict(
            DD_API_KEY="foobar.baz",
            DD_APP_KEY="foobar",
            DD_CIVISIBILITY_AGENTLESS_ENABLED="1",
        )
    ), _dummy_noop_git_client():
        _do_request.return_value = Response(
            status=404,
            body='{"errors": ["Not found"]}',
        )
        CIVisibility.enable(service="test-service")
        assert CIVisibility._instance._api_settings.coverage_enabled is False
        assert CIVisibility._instance._api_settings.skipping_enabled is False
        CIVisibility.disable()


def test_ci_visibility_service_disable():
    with _ci_override_env(
        dict(DD_API_KEY="foobar.baz"),
    ), _dummy_noop_git_client():
        with _patch_dummy_writer():
            dummy_tracer = DummyTracer()
            CIVisibility.enable(tracer=dummy_tracer, service="test-service")
            CIVisibility.disable()
            ci_visibility_instance = CIVisibility._instance
            assert ci_visibility_instance is None
            assert not CIVisibility.enabled


@pytest.mark.parametrize(
    "repository_url,repository_name",
    [
        ("https://github.com/DataDog/dd-trace-py.git", "dd-trace-py"),
        ("https://github.com/DataDog/dd-trace-py", "dd-trace-py"),
        ("git@github.com:DataDog/dd-trace-py.git", "dd-trace-py"),
        ("git@github.com:DataDog/dd-trace-py", "dd-trace-py"),
        ("dd-trace-py", "dd-trace-py"),
        ("git@hostname.com:org/repo-name.git", "repo-name"),
        ("git@hostname.com:org/repo-name", "repo-name"),
        ("ssh://git@hostname.com:org/repo-name", "repo-name"),
        ("git+git://github.com/org/repo-name.git", "repo-name"),
        ("git+ssh://github.com/org/repo-name.git", "repo-name"),
        ("git+https://github.com/org/repo-name.git", "repo-name"),
        ("https://github.com/fastapi/fastapi.git", "fastapi"),
        ("git@github.com:fastapi/fastapi.git", "fastapi"),
        ("git@github.com:fastapi/fastapi.gitttttt", "fastapi.gitttttt"),
        ("git@github.com:fastapi/fastapiiiititititi.git", "fastapiiiititititi"),
        ("https://github.com/fastapi/fastapitttttt.git", "fastapitttttt"),
        ("this is definitely not a valid git repo URL", "this is definitely not a valid git repo URL"),
        ("git@github.com:fastapi/FastAPI.GiT", "FastAPI"),
        ("git+https://github.com/org/REPO-NAME.GIT", "REPO-NAME"),
        ("https://github.com/DataDog/DD-TRACE-py", "DD-TRACE-py"),
        ("https://github.com/DataDog/dd-trace-py.GIT", "dd-trace-py"),
    ],
)
def test_repository_name_extracted(repository_url, repository_name):
    assert _extract_repository_name_from_url(repository_url) == repository_name


def test_repository_name_not_extracted_warning():
    """If provided an invalid repository url, should raise warning and return original repository url"""
    repository_url = "https://github.com:organ[ization/repository-name"
    with mock.patch("ddtrace.internal.ci_visibility.recorder.log") as mock_log:
        extracted_repository_name = _extract_repository_name_from_url(repository_url)
        assert extracted_repository_name == repository_url
    mock_log.warning.assert_called_once_with("Repository name cannot be parsed from repository_url: %s", repository_url)


DUMMY_RESPONSE = Response(status=200, body='{"data": [{"type": "commit", "id": "%s", "attributes": {}}]}' % TEST_SHA_1)


def test_git_client_get_repository_url(git_repo):
    remote_url = CIVisibilityGitClient._get_repository_url(cwd=git_repo)
    assert remote_url == "git@github.com:test-repo-url.git"


def test_git_client_get_repository_url_env_var_precedence(git_repo):
    remote_url = CIVisibilityGitClient._get_repository_url(
        tags={ci.git.REPOSITORY_URL: "https://github.com/Datadog/dd-trace-py"}, cwd=git_repo
    )
    assert remote_url == "https://github.com/Datadog/dd-trace-py"


def test_git_client_get_repository_url_env_var_precedence_empty_tags(git_repo):
    remote_url = CIVisibilityGitClient._get_repository_url(tags={}, cwd=git_repo)
    assert remote_url == "git@github.com:test-repo-url.git"


def test_git_client_get_latest_commits(git_repo):
    with mock.patch(
        "ddtrace.ext.git._git_subprocess_cmd_with_details",
        return_value=_GitSubprocessDetails(
            "b3672ea5cbc584124728c48a443825d2940e0ddd\nb3672ea5cbc584124728c48a443825d2940e0eee", "", 10, 0
        ),
    ) as mock_git_subprocess_cmd_with_details:
        latest_commits = CIVisibilityGitClient._get_latest_commits(cwd=git_repo)
        assert latest_commits == [TEST_SHA_1, TEST_SHA_2]
        mock_git_subprocess_cmd_with_details.assert_called_once_with(
            "log", "--format=%H", "-n", "1000", '--since="1 month ago"', cwd=git_repo
        )


def test_git_client_search_commits():
    remote_url = "git@github.com:test-repo-url.git"
    latest_commits = [TEST_SHA_1]
    serializer = CIVisibilityGitClientSerializerV1("foo")
    backend_commits = CIVisibilityGitClient._search_commits(
        REQUESTS_MODE.AGENTLESS_EVENTS, "", remote_url, latest_commits, serializer, DUMMY_RESPONSE
    )
    assert latest_commits[0] in backend_commits


def test_get_client_do_request_agentless_headers():
    serializer = CIVisibilityGitClientSerializerV1("foo")
    response = mock.MagicMock()
    response.status = 200

    with mock.patch("ddtrace.internal.http.HTTPConnection.request") as _request, mock.patch(
        "ddtrace.internal.http.HTTPConnection.getresponse", return_value=response
    ):
        CIVisibilityGitClient._do_request(
            REQUESTS_MODE.AGENTLESS_EVENTS, "http://base_url", "/endpoint", "payload", serializer, {}
        )

    _request.assert_called_once_with("POST", "/repository/endpoint", "payload", {"dd-api-key": "foo"})


def test_get_client_do_request_evp_proxy_headers():
    serializer = CIVisibilityGitClientSerializerV1("foo")
    response = mock.MagicMock()
    response.status = 200

    with mock.patch("ddtrace.internal.http.HTTPConnection.request") as _request, mock.patch(
        "ddtrace.internal.http.HTTPConnection.getresponse", return_value=response
    ):
        CIVisibilityGitClient._do_request(
            REQUESTS_MODE.EVP_PROXY_EVENTS, "http://base_url", "/endpoint", "payload", serializer, {}
        )

    _request.assert_called_once_with(
        "POST",
        "/repository/endpoint",
        "payload",
        {"X-Datadog-EVP-Subdomain": "api"},
    )


def test_git_client_get_filtered_revisions(git_repo):
    excluded_commits = [TEST_SHA_1]
    filtered_revisions = CIVisibilityGitClient._get_filtered_revisions(excluded_commits, cwd=git_repo)
    assert filtered_revisions == ""


@pytest.mark.parametrize("requests_mode", [REQUESTS_MODE.AGENTLESS_EVENTS, REQUESTS_MODE.EVP_PROXY_EVENTS])
def test_git_client_upload_packfiles(git_repo, requests_mode):
    serializer = CIVisibilityGitClientSerializerV1("foo")
    remote_url = "git@github.com:test-repo-url.git"
    with _build_git_packfiles_with_details("%s\n" % TEST_SHA_1, cwd=git_repo) as (packfiles_path, packfiles_details):
        with mock.patch("ddtrace.internal.ci_visibility.git_client.CIVisibilityGitClient._do_request") as dr:
            dr.return_value = Response(
                status=200,
            )
            CIVisibilityGitClient._upload_packfiles(
                requests_mode, "", remote_url, packfiles_path, serializer, None, cwd=git_repo
            )
            assert dr.call_count == 1
            call_args = dr.call_args_list[0][0]
            call_kwargs = dr.call_args.kwargs
            assert call_args[0] == requests_mode
            assert call_args[1] == ""
            assert call_args[2] == "/packfile"
            assert call_args[3].startswith(b"------------boundary------\r\nContent-Disposition: form-data;")
            assert call_kwargs["headers"] == {"Content-Type": "multipart/form-data; boundary=----------boundary------"}


def test_git_do_request_agentless(git_repo):
    mock_serializer = CIVisibilityGitClientSerializerV1("fakeapikey")
    response = mock.MagicMock()
    setattr(response, "status", 200)  # noqa: B010

    with mock.patch("ddtrace.internal.ci_visibility.git_client.get_connection") as mock_get_connection:
        with mock.patch("ddtrace.internal.http.HTTPConnection.getresponse", return_value=response):
            mock_http_connection = mock.Mock()
            mock_http_connection.request = mock.Mock()
            mock_http_connection.close = mock.Mock()
            mock_get_connection.return_value = mock_http_connection

            CIVisibilityGitClient._do_request(
                REQUESTS_MODE.AGENTLESS_EVENTS,
                "http://base_url",
                "/endpoint",
                '{"payload": "payload"}',
                mock_serializer,
                {"mock_header_name": "mock_header_value"},
            )

            mock_get_connection.assert_any_call("http://base_url/repository/endpoint", timeout=20)
            mock_http_connection.request.assert_called_once_with(
                "POST",
                "/repository/endpoint",
                '{"payload": "payload"}',
                {
                    "dd-api-key": "fakeapikey",
                    "mock_header_name": "mock_header_value",
                },
            )


def test_git_do_request_evp(git_repo):
    mock_serializer = CIVisibilityGitClientSerializerV1("foo")
    response = mock.MagicMock()
    setattr(response, "status", 200)  # noqa: B010

    with mock.patch("ddtrace.internal.ci_visibility.git_client.get_connection") as mock_get_connection:
        with mock.patch("ddtrace.internal.http.HTTPConnection.getresponse", return_value=response):
            mock_http_connection = mock.Mock()
            mock_http_connection.request = mock.Mock()
            mock_http_connection.close = mock.Mock()
            mock_get_connection.return_value = mock_http_connection

            CIVisibilityGitClient._do_request(
                REQUESTS_MODE.EVP_PROXY_EVENTS,
                "https://base_url",
                "/endpoint",
                '{"payload": "payload"}',
                mock_serializer,
                {"mock_header_name": "mock_header_value"},
            )

            mock_get_connection.assert_any_call("https://base_url/repository/endpoint", timeout=20)
            mock_http_connection.request.assert_called_once_with(
                "POST",
                "/repository/endpoint",
                '{"payload": "payload"}',
                {
                    "X-Datadog-EVP-Subdomain": "api",
                    "mock_header_name": "mock_header_value",
                },
            )


class TestCIVisibilityWriter(TracerTestCase):
    def tearDown(self):
        try:
            if CIVisibility.enabled:
                CIVisibility.disable()
        except Exception:
            # no-dd-sa:python-best-practices/no-silent-exception
            pass

    def test_civisibilitywriter_agentless_url(self):
        with _ci_override_env(dict(DD_API_KEY="foobar.baz")):
            with override_global_config({"_ci_visibility_agentless_url": "https://foo.bar"}), mock.patch(
                "ddtrace.internal.ci_visibility.writer.config._ci_visibility_agentless_url", "https://foo.bar"
            ):
                dummy_writer = DummyCIVisibilityWriter()
                assert dummy_writer.intake_url == "https://foo.bar"

    def test_civisibilitywriter_coverage_agentless_url(self):
        ddtrace.internal.ci_visibility.writer.config._ci_visibility_agentless_url = ""
        with _ci_override_env(
            dict(
                DD_API_KEY="foobar.baz",
                DD_CIVISIBILITY_AGENTLESS_ENABLED="1",
            )
        ):
            dummy_writer = DummyCIVisibilityWriter(coverage_enabled=True)
            assert dummy_writer.intake_url == "https://citestcycle-intake.datadoghq.com"

            cov_client = dummy_writer._clients[1]
            assert cov_client._intake_url == "https://citestcov-intake.datadoghq.com"

            with mock.patch("ddtrace.internal.writer.writer.get_connection") as _get_connection:
                _get_connection.return_value.getresponse.return_value.status = 200
                dummy_writer._put("", {}, cov_client, no_trace=True)
                _get_connection.assert_any_call("https://citestcov-intake.datadoghq.com", 2.0)

    def test_civisibilitywriter_coverage_agentless_with_intake_url_param(self):
        ddtrace.internal.ci_visibility.writer.config._ci_visibility_agentless_url = ""
        with _ci_override_env(
            dict(
                DD_API_KEY="foobar.baz",
                DD_CIVISIBILITY_AGENTLESS_ENABLED="1",
            )
        ):
            dummy_writer = DummyCIVisibilityWriter(intake_url="https://some-url.com", coverage_enabled=True)
            assert dummy_writer.intake_url == "https://some-url.com"

            cov_client = dummy_writer._clients[1]
            assert cov_client._intake_url == "https://citestcov-intake.datadoghq.com"

            with mock.patch("ddtrace.internal.writer.writer.get_connection") as _get_connection, mock.patch(
                "ddtrace.internal.ci_visibility.recorder.ddconfig", _get_default_civisibility_ddconfig()
            ):
                _get_connection.return_value.getresponse.return_value.status = 200
                dummy_writer._put("", {}, cov_client, no_trace=True)
                _get_connection.assert_any_call("https://citestcov-intake.datadoghq.com", 2.0)

    def test_civisibilitywriter_coverage_evp_proxy_url(self):
        with _ci_override_env(
            dict(
                DD_API_KEY="foobar.baz",
            )
        ), mock.patch(
            "ddtrace.settings._agent.config.trace_agent_url",
            new_callable=mock.PropertyMock,
            return_value="http://arandomhost:9126",
        ) as agent_url_mock, mock.patch(
            "ddtrace.internal.ci_visibility.recorder.ddconfig", _get_default_civisibility_ddconfig()
        ):
            dummy_writer = DummyCIVisibilityWriter(use_evp=True, coverage_enabled=True)

            test_client = dummy_writer._clients[0]
            assert test_client.ENDPOINT == "/evp_proxy/v2/api/v2/citestcycle"
            cov_client = dummy_writer._clients[1]
            assert cov_client.ENDPOINT == "/evp_proxy/v2/api/v2/citestcov"

            with mock.patch("ddtrace.internal.writer.writer.get_connection") as _get_connection:
                _get_connection.return_value.getresponse.return_value.status = 200
                dummy_writer._put("", {}, cov_client, no_trace=True)
                _get_connection.assert_any_call(agent_url_mock, 2.0)


def test_civisibilitywriter_agentless_url_envvar():
    with _ci_override_env(
        dict(
            DD_API_KEY="foobar.baz",
            DD_CIVISIBILITY_AGENTLESS_URL="https://foo.bar",
            DD_CIVISIBILITY_AGENTLESS_ENABLED="1",
        )
    ), _dummy_noop_git_client(), mock.patch(
        "ddtrace.internal.ci_visibility._api_client._TestVisibilityAPIClientBase.fetch_settings",
        return_value=TestVisibilityAPISettings(False, False, False, False),
    ), mock.patch(
        "ddtrace.internal.ci_visibility.writer.config", Config()
    ), mock.patch(
        "ddtrace.internal.ci_visibility.recorder.ddconfig", _get_default_civisibility_ddconfig()
    ):
        CIVisibility.enable()
        assert CIVisibility._instance._requests_mode == REQUESTS_MODE.AGENTLESS_EVENTS
        assert CIVisibility._instance.tracer._span_aggregator.writer.intake_url == "https://foo.bar"
        CIVisibility.disable()

    def test_civisibilitywriter_evp_proxy_url(self):
        with _ci_override_env(
            dict(
                DD_API_KEY="foobar.baz",
            )
        ), mock.patch(
            "ddtrace.settings._agent.config.trace_agent_url",
            new_callable=mock.PropertyMock,
            return_value="http://evpproxy.bar:1234",
        ), mock.patch("ddtrace.settings._config.Config", _get_default_civisibility_ddconfig()), mock.patch(
            "ddtrace.tracer", CIVisibilityTracer()
        ), mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._agent_evp_proxy_base_url",
            return_value=EVP_PROXY_AGENT_BASE_PATH,
        ), _dummy_noop_git_client(), mock.patch(
            "ddtrace.internal.ci_visibility.writer.config", Config()
        ), mock.patch(
            "ddtrace.internal.ci_visibility.recorder.ddconfig", _get_default_civisibility_ddconfig()
        ):
            CIVisibility.enable()
            assert CIVisibility._instance._requests_mode == REQUESTS_MODE.EVP_PROXY_EVENTS
            assert CIVisibility._instance.tracer._span_aggregator.writer.intake_url == "http://evpproxy.bar:1234"
            CIVisibility.disable()

    def test_civisibilitywriter_only_traces(self):
        with _ci_override_env(
            dict(
                DD_API_KEY="foobar.baz",
            )
        ), mock.patch(
            "ddtrace.settings._agent.config.trace_agent_url",
            new_callable=mock.PropertyMock,
            return_value="http://onlytraces:1234",
        ), mock.patch("ddtrace.tracer", CIVisibilityTracer()), mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._agent_evp_proxy_base_url", return_value=None
        ), mock.patch(
            "ddtrace.internal.ci_visibility.writer.config", Config()
        ), mock.patch(
            "ddtrace.internal.ci_visibility.recorder.ddconfig", _get_default_civisibility_ddconfig()
        ):
            CIVisibility.enable()
            assert CIVisibility._instance._requests_mode == REQUESTS_MODE.TRACES
            assert CIVisibility._instance.tracer._span_aggregator.writer.intake_url == "http://onlytraces:1234"
            CIVisibility.disable()


def test_run_protocol_unshallow_git_ge_227():
    with mock.patch("ddtrace.internal.ci_visibility.git_client.extract_git_version", return_value=(2, 27, 0)):
        with mock.patch.multiple(
            CIVisibilityGitClient,
            _get_repository_url=mock.DEFAULT,
            _is_shallow_repository=classmethod(lambda *args, **kwargs: True),
            _get_latest_commits=classmethod(lambda *args, **kwwargs: ["latest1", "latest2"]),
            _search_commits=classmethod(lambda *args: ["latest1", "searched1", "searched2"]),
            _get_filtered_revisions=mock.DEFAULT,
            _upload_packfiles=mock.DEFAULT,
        ), mock.patch(
            "ddtrace.internal.ci_visibility.git_client._build_git_packfiles_with_details"
        ) as mock_build_packfiles:
            mock_build_packfiles.return_value.__enter__.return_value = "myprefix", _GitSubprocessDetails("", "", 10, 0)
            with mock.patch.object(CIVisibilityGitClient, "_unshallow_repository") as mock_unshallow_repository:
                CIVisibilityGitClient._run_protocol(None, None, None, multiprocessing.Value(c_int, 0))

            mock_unshallow_repository.assert_called_once_with(cwd=None)


def test_run_protocol_does_not_unshallow_git_lt_227():
    with mock.patch("ddtrace.internal.ci_visibility.git_client.extract_git_version", return_value=(2, 26, 0)):
        with mock.patch.multiple(
            CIVisibilityGitClient,
            _get_repository_url=mock.DEFAULT,
            _is_shallow_repository=classmethod(lambda *args, **kwargs: True),
            _get_latest_commits=classmethod(lambda *args, **kwargs: ["latest1", "latest2"]),
            _search_commits=classmethod(lambda *args: ["latest1", "searched1", "searched2"]),
            _get_filtered_revisions=mock.DEFAULT,
            _upload_packfiles=mock.DEFAULT,
        ), mock.patch(
            "ddtrace.internal.ci_visibility.git_client._build_git_packfiles_with_details"
        ) as mock_build_packfiles:
            mock_build_packfiles.return_value.__enter__.return_value = "myprefix", _GitSubprocessDetails("", "", 10, 0)
            with mock.patch.object(CIVisibilityGitClient, "_unshallow_repository") as mock_unshallow_repository:
                CIVisibilityGitClient._run_protocol(None, None, None, multiprocessing.Value(c_int, 0))

            mock_unshallow_repository.assert_not_called()


class TestUploadGitMetadata:
    """Exercises the multprocessed use of CIVisibilityGitClient.upload_git_metadata to make sure that the caller gets
    the expected METADATA_UPLOAD_STATUS value

    This uses CIVisibilityGitClient.wait_for_metadata_upload_status() since it returns the given status, and allows for
    exercising the various exception/error scenarios.
    """

    api_key_requests_mode_parameters = [
        ("myfakeapikey", REQUESTS_MODE.AGENTLESS_EVENTS),
        ("", REQUESTS_MODE.EVP_PROXY_EVENTS),
    ]

    @pytest.fixture(scope="function", autouse=True)
    def mock_git_client(self):
        """NotImplementedError mocks should never be reached unless tests are set up improperly"""
        with mock.patch.multiple(
            CIVisibilityGitClient,
            _is_shallow_repository=mock.Mock(return_value=True),
            _get_latest_commits=mock.Mock(
                side_effect=[["latest1", "latest2"], ["latest1", "latest2", "latest3", "latest4"]]
            ),
            _search_commits=mock.Mock(side_effect=[["latest1"], ["latest1", "latest2"]]),
            _unshallow_repository=mock.Mock(),
            _get_filtered_revisions=mock.Mock(return_value="latest3\nlatest4"),
            _upload_packfiles=mock.Mock(side_effec=NotImplementedError),
            _do_request=mock.Mock(side_effect=NotImplementedError),
        ), mock.patch(
            "ddtrace.internal.ci_visibility.git_client._build_git_packfiles_with_details"
        ) as mock_build_packfiles:
            mock_build_packfiles.return_value.__enter__.return_value = "myprefix", _GitSubprocessDetails("", "", 10, 0)
            yield

    @pytest.fixture(scope="function", autouse=True)
    def _test_context_manager(self):
        with mock.patch(
            "ddtrace.ext.ci.tags",
            return_value={
                "git.repository_url": "git@github.com:TestDog/dd-test-py.git",
                "git.commit.sha": "mytestcommitsha1234",
            },
        ):
            yield

    @pytest.mark.parametrize("api_key, requests_mode", api_key_requests_mode_parameters)
    def test_upload_git_metadata_no_backend_commits_fails(self, api_key, requests_mode):
        with mock.patch.object(CIVisibilityGitClient, "_search_commits", return_value=None):
            git_client = CIVisibilityGitClient(api_key, requests_mode)
            git_client.upload_git_metadata()
            assert git_client.wait_for_metadata_upload_status() == METADATA_UPLOAD_STATUS.FAILED

    @pytest.mark.parametrize("api_key, requests_mode", api_key_requests_mode_parameters)
    def test_upload_git_metadata_no_revisions_succeeds(self, api_key, requests_mode):
        with mock.patch.object(
            CIVisibilityGitClient, "_get_filtered_revisions", classmethod(lambda *args, **kwargs: "")
        ):
            git_client = CIVisibilityGitClient(api_key, requests_mode)
            git_client.upload_git_metadata()
            assert git_client.wait_for_metadata_upload_status() == METADATA_UPLOAD_STATUS.SUCCESS

    @pytest.mark.parametrize("api_key, requests_mode", api_key_requests_mode_parameters)
    def test_upload_git_metadata_upload_packfiles_fails(self, api_key, requests_mode):
        with mock.patch.object(CIVisibilityGitClient, "_upload_packfiles", return_value=False):
            git_client = CIVisibilityGitClient(api_key, requests_mode)
            git_client.upload_git_metadata()
            assert git_client.wait_for_metadata_upload_status() == METADATA_UPLOAD_STATUS.FAILED

    @pytest.mark.parametrize("api_key, requests_mode", api_key_requests_mode_parameters)
    def test_upload_git_metadata_upload_packfiles_succeeds(self, api_key, requests_mode):
        with mock.patch.object(CIVisibilityGitClient, "_upload_packfiles", return_value=True):
            git_client = CIVisibilityGitClient(api_key, requests_mode)
            git_client.upload_git_metadata()
            assert git_client.wait_for_metadata_upload_status() == METADATA_UPLOAD_STATUS.SUCCESS

    @pytest.mark.parametrize("api_key, requests_mode", api_key_requests_mode_parameters)
    def test_upload_git_metadata_upload_nonzero_return_code_fails(self, api_key, requests_mode):
        with mock.patch.object(CIVisibilityGitClient, "_upload_packfiles", return_value=True):
            git_client = CIVisibilityGitClient(api_key, requests_mode)
            git_client.upload_git_metadata()
            assert git_client.wait_for_metadata_upload_status() == METADATA_UPLOAD_STATUS.SUCCESS

    @pytest.mark.parametrize("api_key, requests_mode", api_key_requests_mode_parameters)
    def test_upload_git_metadata_upload_value_error_fails(self, api_key, requests_mode):
        with mock.patch.object(CIVisibilityGitClient, "_upload_packfiles", return_value=True):
            git_client = CIVisibilityGitClient(api_key, requests_mode)
            git_client._worker = mock.Mock(spec=multiprocessing.Process)
            git_client._worker.exitcode = 0
            git_client.upload_git_metadata()
            with pytest.raises(ValueError):
                git_client.wait_for_metadata_upload_status()

    @pytest.mark.parametrize("api_key, requests_mode", api_key_requests_mode_parameters)
    def test_upload_git_metadata_upload_timeout_raises_timeout(self, api_key, requests_mode):
        with mock.patch.object(CIVisibilityGitClient, "upload_git_metadata", lambda *args, **kwargs: time.sleep(3)):
            git_client = CIVisibilityGitClient(api_key, requests_mode)
            git_client._worker = mock.Mock(spec=multiprocessing.Process)
            git_client._worker.exitcode = None
            git_client.upload_git_metadata()
            with pytest.raises(TimeoutError):
                git_client._wait_for_metadata_upload(0)

    @pytest.mark.parametrize("api_key, requests_mode", api_key_requests_mode_parameters)
    def test_upload_git_metadata_upload_unnecessary(self, api_key, requests_mode):
        with mock.patch.object(
            CIVisibilityGitClient, "_get_latest_commits", mock.Mock(side_effect=[["latest1", "latest2"]])
        ), mock.patch.object(CIVisibilityGitClient, "_search_commits", mock.Mock(side_effect=[["latest1", "latest2"]])):
            git_client = CIVisibilityGitClient(api_key, requests_mode)
            git_client.upload_git_metadata()
            assert git_client.wait_for_metadata_upload_status() == METADATA_UPLOAD_STATUS.UNNECESSARY

    @pytest.mark.parametrize("api_key, requests_mode", api_key_requests_mode_parameters)
    def test_upload_git_metadata_upload_no_git_dir(self, api_key, requests_mode):
        with mock.patch.object(CIVisibilityGitClient, "_get_git_dir", mock.Mock(return_value=None)):
            git_client = CIVisibilityGitClient(api_key, requests_mode)
            git_client.upload_git_metadata()

            # Notably, this should _not_ raise ValueError
            assert git_client.wait_for_metadata_upload_status() == METADATA_UPLOAD_STATUS.FAILED


def test_get_filtered_revisions():
    with mock.patch(
        "ddtrace.internal.ci_visibility.git_client._get_rev_list_with_details",
        return_value=_GitSubprocessDetails("rev1\nrev2", "", 100, 0),
    ) as mock_get_rev_list:
        assert (
            CIVisibilityGitClient._get_filtered_revisions(
                ["excluded1", "excluded2"], included_commits=["included1", "included2"], cwd="/path/to/repo"
            )
            == "rev1\nrev2"
        )
        mock_get_rev_list.assert_called_once_with(
            ["excluded1", "excluded2"], ["included1", "included2"], cwd="/path/to/repo"
        )


def test_is_shallow_repository_true():
    with mock.patch(
        "ddtrace.internal.ci_visibility.git_client._is_shallow_repository_with_details", return_value=(True, 10.0, 0)
    ) as mock_is_shallow_repository_with_details:
        assert CIVisibilityGitClient._is_shallow_repository(cwd="/path/to/repo") is True
        mock_is_shallow_repository_with_details.assert_called_once_with(cwd="/path/to/repo")


def test_is_shallow_repository_false():
    with mock.patch(
        "ddtrace.internal.ci_visibility.git_client._is_shallow_repository_with_details", return_value=(False, 10.0, 128)
    ) as mock_is_shallow_repository_with_details:
        assert CIVisibilityGitClient._is_shallow_repository(cwd="/path/to/repo") is False
        mock_is_shallow_repository_with_details.assert_called_once_with(cwd="/path/to/repo")


def test_unshallow_repository_local_head():
    with mock.patch(
        "ddtrace.internal.ci_visibility.git_client._extract_clone_defaultremotename_with_details",
        return_value=_GitSubprocessDetails("origin", "", 100, 0),
    ):
        with mock.patch("ddtrace.internal.ci_visibility.git_client.extract_commit_sha", return_value="myfakesha"):
            with mock.patch("ddtrace.ext.git._git_subprocess_cmd_with_details") as mock_git_subprocess_cmd_with_details:
                CIVisibilityGitClient._unshallow_repository(cwd="/path/to/repo")
                mock_git_subprocess_cmd_with_details.assert_called_once_with(
                    "fetch",
                    '--shallow-since="1 month ago"',
                    "--update-shallow",
                    "--filter=blob:none",
                    "--recurse-submodules=no",
                    "origin",
                    "myfakesha",
                    cwd="/path/to/repo",
                )


def test_unshallow_repository_upstream():
    with mock.patch(
        "ddtrace.internal.ci_visibility.git_client._extract_clone_defaultremotename_with_details",
        return_value=_GitSubprocessDetails("origin", "", 100, 0),
    ):
        with mock.patch(
            "ddtrace.internal.ci_visibility.git_client.CIVisibilityGitClient._unshallow_repository_to_local_head",
            side_effect=ValueError,
        ):
            with mock.patch(
                "ddtrace.internal.ci_visibility.git_client._extract_upstream_sha", return_value="myupstreamsha"
            ):
                with mock.patch(
                    "ddtrace.ext.git._git_subprocess_cmd_with_details"
                ) as mock_git_subprocess_cmd_with_details:
                    CIVisibilityGitClient._unshallow_repository(cwd="/path/to/repo")
                    mock_git_subprocess_cmd_with_details.assert_called_once_with(
                        "fetch",
                        '--shallow-since="1 month ago"',
                        "--update-shallow",
                        "--filter=blob:none",
                        "--recurse-submodules=no",
                        "origin",
                        "myupstreamsha",
                        cwd="/path/to/repo",
                    )


def test_unshallow_repository_full():
    with mock.patch(
        "ddtrace.internal.ci_visibility.git_client._extract_clone_defaultremotename_with_details",
        return_value=_GitSubprocessDetails("origin", "", 100, 0),
    ):
        with mock.patch(
            "ddtrace.internal.ci_visibility.git_client.CIVisibilityGitClient._unshallow_repository_to_local_head",
            side_effect=ValueError,
        ):
            with mock.patch(
                "ddtrace.internal.ci_visibility.git_client.CIVisibilityGitClient._unshallow_repository_to_upstream",
                side_effect=ValueError,
            ):
                with mock.patch(
                    "ddtrace.ext.git._git_subprocess_cmd_with_details", return_value=_GitSubprocessDetails("", "", 0, 0)
                ) as mock_git_subprocess_cmd_with_details:
                    CIVisibilityGitClient._unshallow_repository(cwd="/path/to/repo")
                    mock_git_subprocess_cmd_with_details.assert_called_once_with(
                        "fetch",
                        '--shallow-since="1 month ago"',
                        "--update-shallow",
                        "--filter=blob:none",
                        "--recurse-submodules=no",
                        "origin",
                        cwd="/path/to/repo",
                    )


def test_unshallow_respository_cant_get_remote():
    with mock.patch(
        "ddtrace.internal.ci_visibility.git_client._extract_clone_defaultremotename_with_details",
        return_value=_GitSubprocessDetails("", "", 10, 125),
    ):
        with mock.patch("ddtrace.ext.git._git_subprocess_cmd") as mock_git_subprocess_command:
            CIVisibilityGitClient._unshallow_repository()
            mock_git_subprocess_command.assert_not_called()


def test_encoder_pack_payload():
    packed_payload = CIVisibilityEncoderV01._pack_payload(
        {"string_key": [1, {"unicode_key": "string_value"}, "unicode_value", {"string_key": "unicode_value"}]}
    )
    assert (
        packed_payload == b"\x81\xaastring_key\x94\x01\x81\xabunicode_key\xacstring_value"
        b"\xadunicode_value\x81\xaastring_key\xadunicode_value"
    )


@pytest.mark.parametrize(
    "dd_ci_visibility_agentless_url,expected_url_prefix",
    [("", "https://api.datadoghq.com"), ("https://mycustomurl.com:1234", "https://mycustomurl.com:1234")],
)
def test_fetch_tests_to_skip_custom_configurations(dd_ci_visibility_agentless_url, expected_url_prefix):
    with _ci_override_env(
        dict(
            DD_API_KEY="foobar.baz",
            DD_CIVISIBILITY_AGENTLESS_ENABLED="1",
            DD_CIVISIBILITY_AGENTLESS_URL=dd_ci_visibility_agentless_url,
            DD_TAGS="test.configuration.disk:slow,test.configuration.memory:low",
            DD_SERVICE="test-service",
            DD_ENV="test-env",
        )
    ), mock.patch(
        "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
        return_value=TestVisibilityAPISettings(True, True, False, True),
    ), mock.patch.multiple(
        CIVisibilityGitClient,
        _get_repository_url=classmethod(lambda *args, **kwargs: "git@github.com:TestDog/dd-test-py.git"),
        _is_shallow_repository=classmethod(lambda *args, **kwargs: False),
        _get_latest_commits=classmethod(lambda *args, **kwwargs: ["latest1", "latest2"]),
        _search_commits=classmethod(lambda *args: ["latest1", "searched1", "searched2"]),
        _get_filtered_revisions=classmethod(lambda *args, **kwargs: "revision1\nrevision2"),
        _upload_packfiles=classmethod(lambda *args, **kwargs: None),
    ), mock.patch(
        "ddtrace.ext.ci._get_runtime_and_os_metadata",
        return_value={
            "os.architecture": "testarch64",
            "os.platform": "Not Actually Linux",
            "os.version": "1.2.3-test",
            "runtime.name": "CPythonTest",
            "runtime.version": "1.2.3",
        },
    ), mock.patch(
        "ddtrace.ext.ci.tags",
        return_value={
            "git.repository_url": "git@github.com:TestDog/dd-test-py.git",
            "git.commit.sha": "mytestcommitsha1234",
        },
    ), _dummy_noop_git_client(), mock.patch(
        "ddtrace.internal.ci_visibility._api_client._TestVisibilityAPIClientBase._do_request",
        return_value=Response(
            status=200,
            body='{"data": []}',
        ),
    ) as mock_do_request, mock.patch(
        "ddtrace.internal.ci_visibility.git_client._build_git_packfiles_with_details"
    ) as mock_build_packfiles, mock.patch(
        "ddtrace.internal.ci_visibility.recorder.ddconfig", _get_default_civisibility_ddconfig()
    ), mock.patch(
        "ddtrace.internal.ci_visibility._api_client.uuid4", return_value="checkoutmyuuid4"
    ):
        mock_build_packfiles.return_value.__enter__.return_value = "myprefix", _GitSubprocessDetails("", "", 10, 0)
        CIVisibility.enable(service="test-service")

        expected_data_arg = json.dumps(
            {
                "data": {
                    "id": "checkoutmyuuid4",
                    "type": "test_params",
                    "attributes": {
                        "service": "test-service",
                        "env": "test-env",
                        "repository_url": "git@github.com:TestDog/dd-test-py.git",
                        "sha": "mytestcommitsha1234",
                        "configurations": {
                            "os.architecture": "testarch64",
                            "os.platform": "Not Actually Linux",
                            "os.version": "1.2.3-test",
                            "runtime.name": "CPythonTest",
                            "runtime.version": "1.2.3",
                            "custom": {"disk": "slow", "memory": "low"},
                        },
                        "test_level": "test",
                    },
                }
            }
        )

        assert CIVisibility._instance._api_client._base_url == expected_url_prefix

        mock_do_request.assert_called_once_with(
            "POST",
            "/api/v2/ci/tests/skippable",
            expected_data_arg,
            timeout=20.0,
        )
        CIVisibility.disable()


def test_civisibility_enable_tracer_uses_partial_traces():
    with _ci_override_env(
        dict(
            DD_API_KEY="foobar.baz",
        )
    ), _dummy_noop_git_client(), mock.patch(
        "ddtrace.internal.ci_visibility.recorder.ddconfig", _get_default_civisibility_ddconfig()
    ), mock.patch("ddtrace.internal.ci_visibility.writer.config", Config()):
        CIVisibility.enable()
        assert CIVisibility._instance.tracer._span_aggregator.partial_flush_enabled is True
        assert CIVisibility._instance.tracer._span_aggregator.partial_flush_min_spans == 1
        CIVisibility.disable()


def test_civisibility_enable_respects_passed_in_tracer():
    with _ci_override_env(
        dict(
            DD_API_KEY="foobar.baz",
        )
    ), _dummy_noop_git_client(), mock.patch(
        "ddtrace.internal.ci_visibility.recorder.ddconfig", _get_default_civisibility_ddconfig()
    ), mock.patch("ddtrace.internal.ci_visibility.writer.config", Config()):
        tracer = CIVisibilityTracer()
        tracer._span_aggregator.partial_flush_enabled = False
        tracer._span_aggregator.partial_flush_min_spans = 100
        tracer._recreate()
        CIVisibility.enable(tracer=tracer)
        assert CIVisibility._instance.tracer._span_aggregator.partial_flush_enabled is False
        assert CIVisibility._instance.tracer._span_aggregator.partial_flush_min_spans == 100
        CIVisibility.disable()


class TestIsITRSkippable:
    """Tests whether the CIVisibility.is_item_itr_skippable work properly (the _suite_ and _test_ level methods are
    assumed to be working since they are called by is_item_itr_skippable in a wrapper-like way).

    These tests mock CIVisibility._instance._test_suites_to_skip and _tests_to_skip, implying that
    CIVisibility._fetch_tests_to_skip() ran successfully.

    In test-level skipping mode, only the following tests should be skippable:
        - module_1
            - module_1_suite_1.py
                - test_1
                - test_2
                - test_5[param2] with parameters
        - module_2
            - module_2_suite_1.py
                - test_3
            - module_2_suite_2.py
                - test_2
                - test_4[param1] with parameters
                - test_6[param3] with parameters
                - test_6[param3] without parameters
        - no_module_suite_1.py
            - test_5[param2] with parameters
        - no_module_suite_2.py
            - test_1
            - tests_6[param3] with parameters
            - tests_6[param3] without parameters

    In suite-level skipping mode, only the following suites should skippable:
        - module_1/suite_1.py
        - module_2/suite_1.py
        - module_2/suite_2.py
        - no_module_suite_1.py

    No tests should be skippable in suite-level skipping mode, and vice versa.
    """

    test_level_tests_to_skip: Set[
        ddtrace.internal.test_visibility._internal_item_ids.InternalTestId
    ] = _make_fqdn_test_ids(
        [
            ("module_1", "module_1_suite_1.py", "test_1"),
            ("module_1", "module_1_suite_1.py", "test_2"),
            ("module_1", "module_1_suite_1.py", "test_5[param2]", '{"arg1": "param_arg_1"}'),
            ("module_2", "module_2_suite_1.py", "test_3"),
            ("module_2", "module_2_suite_2.py", "test_2"),
            ("module_2", "module_2_suite_2.py", "test_4[param1]"),
            ("module_2", "module_2_suite_2.py", "test_6[param3]", '{"arg8": "param_arg_8"}'),
            ("module_2", "module_2_suite_2.py", "test_6[param3]"),
            ("", "no_module_suite_1.py", "test_5[param2]", '{"arg9": "param_arg_9"}'),
            ("", "no_module_suite_2.py", "test_1"),
            ("", "no_module_suite_2.py", "test_6[param3]", '{"arg12": "param_arg_12"}'),
            ("", "no_module_suite_2.py", "test_6[param3]"),
        ]
    )

    suite_level_test_suites_to_skip: Set[ext_api.TestSuiteId] = _make_fqdn_suite_ids(
        [
            ("module_1", "module_1_suite_1.py"),
            ("module_2", "module_2_suite_1.py"),
            ("module_2", "module_2_suite_2.py"),
            ("", "no_module_suite_1.py"),
        ]
    )

    # Module 1
    m1 = ext_api.TestModuleId("module_1")
    # Module 1 Suite 1
    m1_s1 = ext_api.TestSuiteId(m1, "module_1_suite_1.py")
    m1_s1_t1 = ddtrace.internal.test_visibility._internal_item_ids.InternalTestId(m1_s1, "test_1")
    m1_s1_t2 = ddtrace.internal.test_visibility._internal_item_ids.InternalTestId(m1_s1, "test_2")
    m1_s1_t3 = ddtrace.internal.test_visibility._internal_item_ids.InternalTestId(m1_s1, "test_3")
    m1_s1_t4 = ddtrace.internal.test_visibility._internal_item_ids.InternalTestId(m1_s1, "test_4[param1]")
    m1_s1_t5 = ddtrace.internal.test_visibility._internal_item_ids.InternalTestId(
        m1_s1, "test_5[param2]", parameters='{"arg1": "param_arg_1"}'
    )
    m1_s1_t6 = ddtrace.internal.test_visibility._internal_item_ids.InternalTestId(
        m1_s1, "test_6[param3]", parameters='{"arg2": "param_arg_2"}'
    )
    m1_s1_t7 = ddtrace.internal.test_visibility._internal_item_ids.InternalTestId(m1_s1, "test_6[param3]")

    # Module 1 Suite 2
    m1_s2 = ext_api.TestSuiteId(m1, "module_1_suite_2.py")
    m1_s2_t1 = ddtrace.internal.test_visibility._internal_item_ids.InternalTestId(m1_s2, "test_1")
    m1_s2_t2 = ddtrace.internal.test_visibility._internal_item_ids.InternalTestId(m1_s2, "test_2")
    m1_s2_t3 = ddtrace.internal.test_visibility._internal_item_ids.InternalTestId(m1_s2, "test_3")
    m1_s2_t4 = ddtrace.internal.test_visibility._internal_item_ids.InternalTestId(m1_s2, "test_4[param1]")
    m1_s2_t5 = ddtrace.internal.test_visibility._internal_item_ids.InternalTestId(
        m1_s2, "test_5[param2]", parameters='{"arg3": "param_arg_3"}'
    )
    m1_s2_t6 = ddtrace.internal.test_visibility._internal_item_ids.InternalTestId(
        m1_s2, "test_6[param3]", parameters='{"arg4": "param_arg_4"}'
    )
    m1_s2_t7 = ddtrace.internal.test_visibility._internal_item_ids.InternalTestId(m1_s2, "test_6[param3]")

    # Module 2
    m2 = ext_api.TestModuleId("module_2")

    # Module 2 Suite 1
    m2_s1 = ext_api.TestSuiteId(m2, "module_2_suite_1.py")
    m2_s1_t1 = ddtrace.internal.test_visibility._internal_item_ids.InternalTestId(m2_s1, "test_1")
    m2_s1_t2 = ddtrace.internal.test_visibility._internal_item_ids.InternalTestId(m2_s1, "test_2")
    m2_s1_t3 = ddtrace.internal.test_visibility._internal_item_ids.InternalTestId(m2_s1, "test_3")
    m2_s1_t4 = ddtrace.internal.test_visibility._internal_item_ids.InternalTestId(m2_s1, "test_4[param1]")
    m2_s1_t5 = ddtrace.internal.test_visibility._internal_item_ids.InternalTestId(
        m2_s1, "test_5[param2]", parameters='{"arg5": "param_arg_5"}'
    )
    m2_s1_t6 = ddtrace.internal.test_visibility._internal_item_ids.InternalTestId(
        m2_s1, "test_6[param3]", parameters='{"arg6": "param_arg_6"}'
    )
    m2_s1_t7 = ddtrace.internal.test_visibility._internal_item_ids.InternalTestId(m2_s1, "test_6[param3]")

    # Module 2 Suite 2
    m2_s2 = ext_api.TestSuiteId(m2, "module_2_suite_2.py")
    m2_s2_t1 = ddtrace.internal.test_visibility._internal_item_ids.InternalTestId(m2_s2, "test_1")
    m2_s2_t2 = ddtrace.internal.test_visibility._internal_item_ids.InternalTestId(m2_s2, "test_2")
    m2_s2_t3 = ddtrace.internal.test_visibility._internal_item_ids.InternalTestId(m2_s2, "test_3")
    m2_s2_t4 = ddtrace.internal.test_visibility._internal_item_ids.InternalTestId(m2_s2, "test_4[param1]")
    m2_s2_t5 = ddtrace.internal.test_visibility._internal_item_ids.InternalTestId(
        m2_s2, "test_5[param2]", parameters='{"arg7": "param_arg_7"}'
    )
    m2_s2_t6 = ddtrace.internal.test_visibility._internal_item_ids.InternalTestId(
        m2_s2, "test_6[param3]", parameters='{"arg8": "param_arg_8"}'
    )
    m2_s2_t7 = ddtrace.internal.test_visibility._internal_item_ids.InternalTestId(m2_s2, "test_6[param3]")

    # Module 3
    m3 = ext_api.TestModuleId("")
    m3_s1 = ext_api.TestSuiteId(m3, "no_module_suite_1.py")
    m3_s1_t1 = ddtrace.internal.test_visibility._internal_item_ids.InternalTestId(m3_s1, "test_1")
    m3_s1_t2 = ddtrace.internal.test_visibility._internal_item_ids.InternalTestId(m3_s1, "test_2")
    m3_s1_t3 = ddtrace.internal.test_visibility._internal_item_ids.InternalTestId(m3_s1, "test_3")
    m3_s1_t4 = ddtrace.internal.test_visibility._internal_item_ids.InternalTestId(m3_s1, "test_4[param1]")
    m3_s1_t5 = ddtrace.internal.test_visibility._internal_item_ids.InternalTestId(
        m3_s1, "test_5[param2]", parameters='{"arg9": "param_arg_9"}'
    )
    m3_s1_t6 = ddtrace.internal.test_visibility._internal_item_ids.InternalTestId(
        m3_s1, "test_6[param3]", parameters='{"arg10": "param_arg_10"}'
    )
    m3_s1_t7 = ddtrace.internal.test_visibility._internal_item_ids.InternalTestId(m3_s1, "test_6[param3]")

    m3_s2 = ext_api.TestSuiteId(m3, "no_module_suite_2.py")
    m3_s2_t1 = ddtrace.internal.test_visibility._internal_item_ids.InternalTestId(m3_s2, "test_1")
    m3_s2_t2 = ddtrace.internal.test_visibility._internal_item_ids.InternalTestId(m3_s2, "test_2")
    m3_s2_t3 = ddtrace.internal.test_visibility._internal_item_ids.InternalTestId(m3_s2, "test_3")
    m3_s2_t4 = ddtrace.internal.test_visibility._internal_item_ids.InternalTestId(m3_s2, "test_4[param1]")
    m3_s2_t5 = ddtrace.internal.test_visibility._internal_item_ids.InternalTestId(
        m3_s2, "test_5[param2]", parameters='{"arg11": "param_arg_11"}'
    )
    m3_s2_t6 = ddtrace.internal.test_visibility._internal_item_ids.InternalTestId(
        m3_s2, "test_6[param3]", parameters='{"arg12": "param_arg_12"}'
    )
    m3_s2_t7 = ddtrace.internal.test_visibility._internal_item_ids.InternalTestId(m3_s2, "test_6[param3]")

    def _get_all_suite_ids(self):
        return {getattr(self, suite_id) for suite_id in vars(self.__class__) if re.match(r"^m\d_s\d$", suite_id)}

    def _get_all_test_ids(self):
        return {getattr(self, test_id) for test_id in vars(self.__class__) if re.match(r"^m\d_s\d_t\d$", test_id)}

    def test_is_item_itr_skippable_test_level(self):
        with mock.patch.object(CIVisibility, "enabled", True), mock.patch.object(
            CIVisibility, "_instance", Mock()
        ) as mock_instance:
            mock_instance._itr_data = ITRData(skippable_items=self.test_level_tests_to_skip)
            mock_instance._suite_skipping_mode = False

            expected_skippable_test_ids = {
                self.m1_s1_t1,
                self.m1_s1_t2,
                self.m1_s1_t5,
                self.m2_s1_t3,
                self.m2_s2_t2,
                self.m2_s2_t4,
                self.m2_s2_t6,
                self.m2_s2_t7,
                self.m3_s1_t5,
                self.m3_s2_t1,
                self.m3_s2_t6,
                self.m3_s2_t7,
            }
            expected_non_skippable_test_ids = self._get_all_test_ids() - expected_skippable_test_ids

            assert CIVisibility._instance is not None

            # Check skippable tests are correct
            for test_id in expected_skippable_test_ids:
                assert CIVisibility.is_item_itr_skippable(test_id) is True

            # Check non-skippable tests are correct
            for test_id in expected_non_skippable_test_ids:
                assert CIVisibility.is_item_itr_skippable(test_id) is False

            # Check all suites are not skippable
            for suite_id in self._get_all_suite_ids():
                assert CIVisibility.is_item_itr_skippable(suite_id) is False

    def test_is_item_itr_skippable_suite_level(self):
        with mock.patch.object(CIVisibility, "enabled", True), mock.patch.object(
            CIVisibility, "_instance", Mock()
        ) as mock_instance:
            mock_instance._itr_data = ITRData(skippable_items=self.suite_level_test_suites_to_skip)
            mock_instance._suite_skipping_mode = True

            expected_skippable_suite_ids = {self.m1_s1, self.m2_s1, self.m2_s2, self.m3_s1}
            expected_non_skippable_suite_ids = self._get_all_suite_ids() - set(expected_skippable_suite_ids)

            assert CIVisibility._instance is not None

            # Check skippable suites are correct
            for suite_id in expected_skippable_suite_ids:
                assert CIVisibility.is_item_itr_skippable(suite_id) is True

            # Check non-skippable suites are correct
            for suite_id in expected_non_skippable_suite_ids:
                assert CIVisibility.is_item_itr_skippable(suite_id) is False

            # Check all tests are not skippable
            for test_id in self._get_all_test_ids():
                assert CIVisibility.is_item_itr_skippable(test_id) is False


class TestCIVisibilitySetTestSessionName(TracerTestCase):
    def tearDown(self):
        try:
            if CIVisibility.enabled:
                CIVisibility.disable()
        except Exception:
            # no-dd-sa:python-best-practices/no-silent-exception
            pass

    def assert_test_session_name(self, name):
        """Check that the payload metadata contains the test session name attributes."""
        payload = msgpack.loads(
            CIVisibility._instance.tracer._span_aggregator.writer._clients[0].encoder._build_payload([[Span("foo")]])
        )
        assert payload["metadata"]["test_session_end"] == {"test_session.name": name}
        assert payload["metadata"]["test_suite_end"] == {"test_session.name": name}
        assert payload["metadata"]["test_module_end"] == {"test_session.name": name}
        assert payload["metadata"]["test"] == {"test_session.name": name}

    def test_set_test_session_name_from_command(self):
        """When neither DD_TEST_SESSION_NAME nor a job id is provided, the test session name should be the test
        command.
        """
        with _ci_override_env(dict()), set_up_mock_civisibility(), _patch_dummy_writer():
            CIVisibility.enable()
            CIVisibility.set_test_session_name(test_command="some_command")
        self.assert_test_session_name("some_command")

    def test_set_test_session_name_from_dd_test_session_name_env_var(self):
        """When DD_TEST_SESSION_NAME is provided, the test session name should be its value."""
        with _ci_override_env(
            dict(
                DD_TEST_SESSION_NAME="the_name",
            )
        ), set_up_mock_civisibility(), _patch_dummy_writer():
            CIVisibility.enable()
            CIVisibility.set_test_session_name(test_command="some_command")
        self.assert_test_session_name("the_name")

    def test_set_test_session_name_from_job_name_and_command(self):
        """When DD_TEST_SESSION_NAME is not provided, but a job id is, the test session name should be constructed from
        the job id and test command.
        """
        with _ci_override_env(
            dict(
                GITLAB_CI="1",
                CI_JOB_NAME="the_job",
            )
        ), set_up_mock_civisibility(), _patch_dummy_writer():
            CIVisibility.enable()
            CIVisibility.set_test_session_name(test_command="some_command")
        self.assert_test_session_name("the_job-some_command")

    def test_set_test_session_name_from_dd_test_session_name_env_var_priority(self):
        """When both DD_TEST_SESSION_NAME and job id are provided, DD_TEST_SESSION_NAME wins."""
        with _ci_override_env(
            dict(
                GITLAB_CI="1",
                CI_JOB_NAME="the_job",
                DD_TEST_SESSION_NAME="the_name",
            )
        ), set_up_mock_civisibility(), _patch_dummy_writer():
            CIVisibility.enable()
            CIVisibility.set_test_session_name(test_command="some_command")
        self.assert_test_session_name("the_name")


class TestCIVisibilityLibraryCapabilities(TracerTestCase):
    def tearDown(self):
        try:
            if CIVisibility.enabled:
                CIVisibility.disable()
        except Exception:
            # no-dd-sa:python-best-practices/no-silent-exception
            pass

    def test_set_library_capabilities(self):
        with _ci_override_env(), set_up_mock_civisibility(), _patch_dummy_writer():
            CIVisibility.enable()
            CIVisibility.set_library_capabilities(
                LibraryCapabilities(
                    early_flake_detection="1",
                    auto_test_retries=None,
                    test_impact_analysis="2",
                )
            )

        payload = msgpack.loads(
            CIVisibility._instance.tracer._span_aggregator.writer._clients[0].encoder._build_payload([[Span("foo")]])
        )
        assert payload["metadata"]["test"] == {
            "_dd.library_capabilities.early_flake_detection": "1",
            "_dd.library_capabilities.test_impact_analysis": "2",
        }
