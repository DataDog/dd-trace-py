import json
import typing as t
from unittest import mock

import pytest

import ddtrace
from ddtrace.ext import ci
from ddtrace.ext.test_visibility import ITR_SKIPPING_LEVEL
from ddtrace.ext.test_visibility._item_ids import TestModuleId
from ddtrace.ext.test_visibility._item_ids import TestSuiteId
from ddtrace.internal.ci_visibility import CIVisibility
from ddtrace.internal.ci_visibility._api_client import AgentlessTestVisibilityAPIClient
from ddtrace.internal.ci_visibility._api_client import EVPProxyTestVisibilityAPIClient
from ddtrace.internal.ci_visibility.constants import EVP_PROXY_AGENT_BASE_PATH_V4
from ddtrace.internal.ci_visibility.constants import REQUESTS_MODE
from ddtrace.internal.ci_visibility.git_client import CIVisibilityGitClient
from ddtrace.internal.ci_visibility.git_data import GitData
from ddtrace.internal.test_visibility._internal_item_ids import InternalTestId
from ddtrace.internal.utils.http import Response


_AGENTLESS = REQUESTS_MODE.AGENTLESS_EVENTS
_EVP_PROXY = REQUESTS_MODE.EVP_PROXY_EVENTS


def _get_mock_connection(body):
    class MockHttpResponse:
        status = 200

        def read(self):
            return body

    mock_connection = mock.Mock()
    mock_connection.getresponse.return_value = MockHttpResponse()
    mock_connection.request = mock.Mock()
    mock_connection.close = mock.Mock()

    return mock_connection


def _get_setting_api_response(
    status_code=200,
    code_coverage=False,
    tests_skipping=False,
    require_git=False,
    itr_enabled=False,
    flaky_test_retries_enabled=False,
    efd_present=False,  # This controls whether a default EFD response is present (instead of only {"enabled": false}
    efd_detection_enabled=False,
    known_tests_enabled=False,
    efd_5s=10,
    efd_10s=5,
    efd_30s=3,
    efd_5m=2,
    faulty_session_threshold=30,
):
    body = {
        "data": {
            "id": "1234",
            "type": "ci_app_tracers_test_service_settings",
            "attributes": {
                "code_coverage": code_coverage,
                "early_flake_detection": {
                    "enabled": False,
                },
                "flaky_test_retries_enabled": flaky_test_retries_enabled,
                "itr_enabled": itr_enabled,
                "require_git": require_git,
                "tests_skipping": tests_skipping,
                "known_tests_enabled": known_tests_enabled,
            },
        }
    }

    if efd_present or efd_detection_enabled:
        body["data"]["attributes"]["early_flake_detection"].update(
            {
                "enabled": efd_detection_enabled,
                "slow_test_retries": {"10s": efd_10s, "30s": efd_30s, "5m": efd_5m, "5s": efd_5s},
                "faulty_session_threshold": faulty_session_threshold,
            }
        )

    return Response(status=status_code, body=json.dumps(body))


def _get_skippable_api_response():
    return Response(
        200,
        json.dumps(
            {
                "data": [],
                "meta": {
                    "correlation_id": "1234ideclareacorrelationid",
                },
            }
        ),
    )


def _get_tests_api_response(tests_body: t.Optional[t.Dict] = None):
    response = {"data": {"id": "J0ucvcSApX8", "type": "ci_app_libraries_tests", "attributes": {"tests": {}}}}

    if tests_body is not None:
        response["data"]["attributes"]["tests"].update(tests_body)

    return Response(200, json.dumps(response))


def _make_fqdn_internal_test_id(module_name: str, suite_name: str, test_name: str, parameters: t.Optional[str] = None):
    """An easy way to create a test id "from the bottom up"

    This is useful for mass-making test ids.
    """
    return InternalTestId(TestSuiteId(TestModuleId(module_name), suite_name), test_name, parameters)


def _make_fqdn_test_ids(test_descs: t.List[t.Union[t.Tuple[str, str, str], t.Tuple[str, str, str, str]]]):
    """An easy way to make multiple test ids"""
    return {
        _make_fqdn_internal_test_id(
            test_desc[0], test_desc[1], test_desc[2], test_desc[3] if len(test_desc) == 4 else None
        )
        for test_desc in test_descs
    }


def _make_fqdn_suite_id(module_name: str, suite_name: str):
    return TestSuiteId(TestModuleId(module_name), suite_name)


def _make_fqdn_suite_ids(suite_descs: t.List[t.Tuple[str, str]]):
    return {_make_fqdn_suite_id(suite_desc[0], suite_desc[1]) for suite_desc in suite_descs}


class TestTestVisibilityAPIClientBase:
    """Tests that the TestVisibility API clients
    - make calls to the correct backend URL
    - send the correct payload
    - handle API responses correctly

    Across a "matrix" of:
    - requests mode (agentless, EVP proxy)
    - overrides (custom agent URL, custom agentless URL)
    - good/bad/incorrect API responses
    """

    @pytest.fixture(scope="function", autouse=True)
    def _disable_ci_visibility(self):
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

    default_git_data = GitData("my_repo_url", "some_branch", "mycommitshaaaaaaalalala", "some message")

    default_configurations = {
        "os.architecture": "arm64",
        "os.platform": "PlatForm",
        "os.version": "9.8.a.b",
        "runtime.name": "RPython",
        "runtime.version": "11.5.2",
    }

    def _get_test_client(
        self,
        itr_skipping_level: ITR_SKIPPING_LEVEL = ITR_SKIPPING_LEVEL.TEST,
        requests_mode: REQUESTS_MODE = _AGENTLESS,
        git_data: GitData = None,
        api_key: t.Optional[str] = "my_api_key",
        dd_site: t.Optional[str] = None,
        agentless_url: t.Optional[str] = None,
        agent_url: t.Optional[str] = "http://agenturl:1234",
        dd_service: t.Optional[str] = None,
        dd_env: t.Optional[str] = None,
        client_timeout: t.Optional[float] = None,
    ):
        git_data = git_data if git_data is not None else self.default_git_data

        if requests_mode == _AGENTLESS:
            return AgentlessTestVisibilityAPIClient(
                itr_skipping_level,
                git_data,
                self.default_configurations,
                api_key,
                dd_site,
                agentless_url,
                dd_service,
                dd_env,
                client_timeout,
            )
        # no-dd-sa:python-best-practices/if-return-no-else
        else:
            return EVPProxyTestVisibilityAPIClient(
                itr_skipping_level,
                git_data,
                self.default_configurations,
                agent_url,
                dd_service,
                dd_env,
                client_timeout,
                evp_proxy_base_url=EVP_PROXY_AGENT_BASE_PATH_V4,
            )

    def _get_expected_do_request_setting_payload(
        self,
        itr_skipping_level: ITR_SKIPPING_LEVEL = ITR_SKIPPING_LEVEL.TEST,
        git_data: GitData = None,
        dd_service: t.Optional[str] = None,
        dd_env: t.Optional[str] = None,
    ):
        git_data = self.default_git_data if git_data is None else git_data

        return {
            "data": {
                "id": "checkoutmyuuid4",
                "type": "ci_app_test_service_libraries_settings",
                "attributes": {
                    "test_level": "test" if itr_skipping_level == ITR_SKIPPING_LEVEL.TEST else "suite",
                    "service": dd_service,
                    "env": dd_env,
                    "repository_url": git_data.repository_url,
                    "sha": git_data.commit_sha,
                    "branch": git_data.branch,
                    "configurations": {
                        "os.architecture": "arm64",
                        "os.platform": "PlatForm",
                        "os.version": "9.8.a.b",
                        "runtime.name": "RPython",
                        "runtime.version": "11.5.2",
                    },
                },
            },
        }

    def _get_expected_do_request_skippable_payload(
        self,
        itr_skipping_level: ITR_SKIPPING_LEVEL = ITR_SKIPPING_LEVEL.TEST,
        git_data: GitData = None,
        dd_service: t.Optional[str] = None,
        dd_env: t.Optional[str] = None,
    ):
        git_data = self.default_git_data if git_data is None else git_data

        return {
            "data": {
                "id": "checkoutmyuuid4",
                "type": "test_params",
                "attributes": {
                    "test_level": "test" if itr_skipping_level == ITR_SKIPPING_LEVEL.TEST else "suite",
                    "service": dd_service,
                    "env": dd_env,
                    "repository_url": git_data.repository_url,
                    "sha": git_data.commit_sha,
                    "configurations": {
                        "os.architecture": "arm64",
                        "os.platform": "PlatForm",
                        "os.version": "9.8.a.b",
                        "runtime.name": "RPython",
                        "runtime.version": "11.5.2",
                    },
                },
            },
        }

    def _get_expected_do_request_tests_payload(
        self,
        repository_url: str = None,
        dd_service: t.Optional[str] = None,
        dd_env: t.Optional[str] = None,
    ):
        if repository_url is None:
            repository_url = self.default_git_data.repository_url

        return {
            "data": {
                "id": "checkoutmyuuid4",
                "type": "ci_app_libraries_tests_request",
                "attributes": {
                    "service": dd_service,
                    "env": dd_env,
                    "repository_url": repository_url,
                    "configurations": {
                        "os.architecture": "arm64",
                        "os.platform": "PlatForm",
                        "os.version": "9.8.a.b",
                        "runtime.name": "RPython",
                        "runtime.version": "11.5.2",
                    },
                },
            },
        }

    @staticmethod
    def _get_mock_civisibility(requests_mode, suite_skipping_mode):
        with mock.patch.object(CIVisibility, "__init__", return_value=None):
            mock_civisibility = CIVisibility()

            # User-configurable values
            mock_civisibility._requests_mode = requests_mode
            mock_civisibility._suite_skipping_mode = suite_skipping_mode
            if requests_mode == REQUESTS_MODE.AGENTLESS_EVENTS:
                mock_civisibility._api_client = mock.Mock(spec=AgentlessTestVisibilityAPIClient)
            else:
                mock_civisibility._api_client = mock.Mock(spec=EVPProxyTestVisibilityAPIClient)

            # Defaults
            mock_civisibility._service = "service"
            mock_civisibility._api_key = "myfakeapikey"
            mock_civisibility._dd_site = "datad0g.com"
            mock_civisibility._tags = {
                ci.git.REPOSITORY_URL: "my_repo_url",
                ci.git.COMMIT_SHA: "mycommitshaaaaaaalalala",
                ci.git.BRANCH: "notmain",
            }
            mock_civisibility._configurations = {
                "os.architecture": "arm64",
                "os.platform": "PlatForm",
                "os.version": "9.8.a.b",
                "runtime.name": "RPython",
                "runtime.version": "11.5.2",
            }
            mock_civisibility._git_client = mock.Mock(spec=CIVisibilityGitClient)
            mock_civisibility.tracer = mock.Mock(spec=ddtrace.trace.Tracer)
            mock_civisibility.tracer._agent_url = "http://notahost:1234"

        return mock_civisibility

    @pytest.fixture(scope="function", autouse=True)
    def _test_context_manager(self):
        with mock.patch("ddtrace.internal.ci_visibility._api_client.uuid4", return_value="checkoutmyuuid4"), mock.patch(
            "ddtrace.internal.ci_visibility._api_client.DEFAULT_TIMEOUT", 12.34
        ), mock.patch("ddtrace.internal.ci_visibility._api_client.DEFAULT_ITR_SKIPPABLE_TIMEOUT", 43.21):
            yield
