import abc
from base64 import b64decode
import dataclasses
from http.client import RemoteDisconnected
import json
from json import JSONDecodeError
import os
import socket
import typing as t
from uuid import uuid4

from ddtrace.ext.test_visibility import ITR_SKIPPING_LEVEL
from ddtrace.ext.test_visibility._test_visibility_base import TestId
from ddtrace.ext.test_visibility._test_visibility_base import TestModuleId
from ddtrace.ext.test_visibility._test_visibility_base import TestSuiteId
from ddtrace.internal.ci_visibility.constants import AGENTLESS_API_KEY_HEADER_NAME
from ddtrace.internal.ci_visibility.constants import AGENTLESS_DEFAULT_SITE
from ddtrace.internal.ci_visibility.constants import EVP_PROXY_AGENT_BASE_PATH
from ddtrace.internal.ci_visibility.constants import EVP_SUBDOMAIN_HEADER_API_VALUE
from ddtrace.internal.ci_visibility.constants import EVP_SUBDOMAIN_HEADER_NAME
from ddtrace.internal.ci_visibility.constants import KNOWN_TESTS_ENDPOINT
from ddtrace.internal.ci_visibility.constants import REQUESTS_MODE
from ddtrace.internal.ci_visibility.constants import SETTING_ENDPOINT
from ddtrace.internal.ci_visibility.constants import SKIPPABLE_ENDPOINT
from ddtrace.internal.ci_visibility.constants import SUITE
from ddtrace.internal.ci_visibility.constants import TEST
from ddtrace.internal.ci_visibility.constants import TEST_MANAGEMENT_TESTS_ENDPOINT
from ddtrace.internal.ci_visibility.errors import CIVisibilityAuthenticationException
from ddtrace.internal.ci_visibility.git_data import GitData
from ddtrace.internal.ci_visibility.telemetry.api_request import APIRequestMetricNames
from ddtrace.internal.ci_visibility.telemetry.api_request import record_api_request
from ddtrace.internal.ci_visibility.telemetry.api_request import record_api_request_error
from ddtrace.internal.ci_visibility.telemetry.constants import ERROR_TYPES
from ddtrace.internal.ci_visibility.telemetry.constants import GIT_TELEMETRY
from ddtrace.internal.ci_visibility.telemetry.early_flake_detection import EARLY_FLAKE_DETECTION_TELEMETRY
from ddtrace.internal.ci_visibility.telemetry.early_flake_detection import record_early_flake_detection_tests_count
from ddtrace.internal.ci_visibility.telemetry.git import record_settings_response
from ddtrace.internal.ci_visibility.telemetry.itr import SKIPPABLE_TESTS_TELEMETRY
from ddtrace.internal.ci_visibility.telemetry.itr import record_skippable_count
from ddtrace.internal.ci_visibility.telemetry.test_management import TEST_MANAGEMENT_TELEMETRY
from ddtrace.internal.ci_visibility.telemetry.test_management import record_test_management_tests_count
from ddtrace.internal.ci_visibility.utils import combine_url_path
from ddtrace.internal.logger import get_logger
from ddtrace.internal.test_visibility.coverage_lines import CoverageLines
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.http import ConnectionType
from ddtrace.internal.utils.http import Response
from ddtrace.internal.utils.http import get_connection
from ddtrace.internal.utils.http import verify_url
from ddtrace.internal.utils.time import StopWatch


# TypedDict was added to typing in python 3.8
try:
    from typing import TypedDict  # noqa:F401
except ImportError:
    from typing_extensions import TypedDict

log = get_logger(__name__)

DEFAULT_TIMEOUT: float = 15.0
DEFAULT_ITR_SKIPPABLE_TIMEOUT: float = 20.0
DEFAULT_ATTEMPT_TO_FIX_RETRIES: int = 20

_BASE_HEADERS: t.Dict[str, str] = {
    "Content-Type": "application/json",
}

_SKIPPABLE_ITEM_ID_TYPE = t.Union[TestId, TestSuiteId]
_CONFIGURATIONS_TYPE = t.Dict[str, t.Union[str, t.Dict[str, str]]]
_KNOWN_TESTS_TYPE = t.Set[TestId]

_NETWORK_ERRORS = (TimeoutError, socket.timeout, RemoteDisconnected)


class TestVisibilitySettingsError(Exception):
    __test__ = False
    pass


class TestVisibilitySkippableItemsError(Exception):
    __test__ = False
    pass


@dataclasses.dataclass(frozen=True)
class EarlyFlakeDetectionSettings:
    enabled: bool = False
    slow_test_retries_5s: int = 10
    slow_test_retries_10s: int = 5
    slow_test_retries_30s: int = 3
    slow_test_retries_5m: int = 2
    faulty_session_threshold: int = 30


@dataclasses.dataclass(frozen=True)
class TestManagementSettings:
    enabled: bool = False
    attempt_to_fix_retries: int = DEFAULT_ATTEMPT_TO_FIX_RETRIES

    __test__ = False


@dataclasses.dataclass(frozen=True)
class TestProperties:
    quarantined: bool = False
    disabled: bool = False
    attempt_to_fix: bool = False

    __test__ = False


@dataclasses.dataclass(frozen=True)
class TestVisibilityAPISettings:
    __test__ = False
    coverage_enabled: bool = False
    skipping_enabled: bool = False
    require_git: bool = False
    itr_enabled: bool = False
    flaky_test_retries_enabled: bool = False
    known_tests_enabled: bool = False
    early_flake_detection: EarlyFlakeDetectionSettings = dataclasses.field(default_factory=EarlyFlakeDetectionSettings)
    test_management: TestManagementSettings = dataclasses.field(default_factory=TestManagementSettings)


@dataclasses.dataclass(frozen=True)
class ITRData:
    correlation_id: t.Optional[str] = None
    covered_files: t.Optional[t.Dict[str, CoverageLines]] = None
    skippable_items: t.Set[t.Union[TestId, TestSuiteId]] = dataclasses.field(default_factory=set)


class _SkippableResponseMeta(TypedDict):
    coverage: t.Dict[str, str]
    correlation_id: str


class _SkippableResponseDataItemAttributes(TypedDict):
    name: str
    suite: str
    parameters: str
    configurations: t.Dict[str, t.Any]


class _SkippableResponseDataItem(TypedDict):
    type: str
    attributes: _SkippableResponseDataItemAttributes


class _SkippableResponse(TypedDict):
    data: t.List[_SkippableResponseDataItem]
    meta: _SkippableResponseMeta


def _get_test_id_from_skippable_test(skippable_test: _SkippableResponseDataItem, ignore_parameters: bool) -> TestId:
    test_type = skippable_test["type"]
    if test_type != TEST:
        raise ValueError(f"Test type {test_type} is not expected test type {TEST}")
    module_id = TestModuleId(skippable_test["attributes"]["configurations"]["test.bundle"])
    suite_id = TestSuiteId(module_id, skippable_test["attributes"]["suite"])
    test_name = skippable_test["attributes"]["name"]
    test_parameters = None if ignore_parameters else skippable_test["attributes"].get("parameters")
    return TestId(suite_id, test_name, test_parameters)


def _get_suite_id_from_skippable_suite(skippable_suite: _SkippableResponseDataItem) -> TestSuiteId:
    suite_type = skippable_suite["type"]
    if suite_type != SUITE:
        raise ValueError(f"Test type {suite_type} is not expected test type {SUITE}")

    module_id = TestModuleId(skippable_suite["attributes"]["configurations"]["test.bundle"])
    return TestSuiteId(module_id, skippable_suite["attributes"]["suite"])


def _parse_covered_files(covered_files_data: t.Dict[str, str]) -> t.Optional[t.Dict[str, CoverageLines]]:
    covered_files = {}
    parse_errors = 0
    for covered_file, covered_lines_bytes in covered_files_data.items():
        try:
            covered_lines = CoverageLines.from_bytearray(bytearray(b64decode(covered_lines_bytes)))
            covered_files[covered_file] = covered_lines
        except Exception:  # noqa: E722
            log.debug("Failed to parse coverage data for file %s", covered_file)
            parse_errors += 1
            continue

    if parse_errors > 0:
        log.warning("Failed to parse %d coverage files", parse_errors)

    return covered_files


def _parse_skippable_suites(
    skippable_suites_data: t.List[_SkippableResponseDataItem],
) -> t.Set[_SKIPPABLE_ITEM_ID_TYPE]:
    suites_to_skip: t.Set[_SKIPPABLE_ITEM_ID_TYPE] = set()
    count_unparsed_suites = 0
    for skippable_suite in skippable_suites_data:
        try:
            suite_id = _get_suite_id_from_skippable_suite(skippable_suite)
            suites_to_skip.add(suite_id)
        except Exception:  # noqa: E722
            count_unparsed_suites += 1
            log.debug("Failed to parse skippable suite: %s", skippable_suite, exc_info=True)

    if count_unparsed_suites:
        log.warning("Failed to parse %d skippable suites", count_unparsed_suites)

    record_skippable_count(len(suites_to_skip), SUITE)

    return suites_to_skip


def _parse_skippable_tests(
    skippable_tests_data: t.List[_SkippableResponseDataItem], ignore_parameters: bool = False
) -> t.Set[_SKIPPABLE_ITEM_ID_TYPE]:
    tests_to_skip: t.Set[_SKIPPABLE_ITEM_ID_TYPE] = set()
    count_unparsed_tests = 0
    for skippable_test in skippable_tests_data:
        try:
            test_id = _get_test_id_from_skippable_test(skippable_test, ignore_parameters)
            tests_to_skip.add(test_id)
        except Exception:  # noqa: E722
            log.warning("Failed to parse skippable test: %s", skippable_test, exc_info=True)
            count_unparsed_tests += 1

    if count_unparsed_tests:
        log.warning("Failed to parse %d skippable tests", count_unparsed_tests)

    record_skippable_count(len(tests_to_skip), TEST)

    return tests_to_skip


class _TestVisibilityAPIClientBase(abc.ABC):
    """Client for fetching test visibility settings from the CI Visibility API

    This class makes no direct references to environment variables, configs, or settings not passed to its
    constructor (except for default values).
    """

    _requests_mode: REQUESTS_MODE

    def __init__(
        self,
        base_url: str,
        itr_skipping_level: ITR_SKIPPING_LEVEL,
        git_data: GitData,
        configurations: t.Dict[str, t.Any],
        dd_service: t.Optional[str] = None,
        dd_env: t.Optional[str] = None,
        timeout: t.Optional[float] = None,
    ):
        self._base_url: str = base_url
        self._itr_skipping_level: ITR_SKIPPING_LEVEL = itr_skipping_level
        self._git_data: GitData = git_data
        self._configurations: _CONFIGURATIONS_TYPE = configurations
        self._service: t.Optional[str] = dd_service
        self._dd_env: t.Optional[str] = dd_env
        self._timeout: float = timeout if timeout is not None else DEFAULT_TIMEOUT

    @abc.abstractmethod
    def _redact_headers(self) -> t.Dict[str, str]:
        """This is an abstract method to force child classes to consider which headers should be redacted for logging"""
        pass

    @abc.abstractmethod
    def _get_headers(self) -> t.Dict[str, str]:
        pass

    def _get_final_headers(self) -> t.Dict[str, str]:
        headers = _BASE_HEADERS.copy()
        headers.update(self._get_headers())
        return headers

    def _do_request(self, method: str, endpoint: str, payload: str, timeout: t.Optional[float] = None) -> Response:
        timeout = timeout if timeout is not None else self._timeout
        headers = self._get_final_headers()
        url = combine_url_path(self._base_url, endpoint)

        conn: t.Optional[ConnectionType] = None
        try:
            parsed_url = verify_url(url)
            url_path = parsed_url.path
            conn = get_connection(url, timeout)

            log.debug(
                "Sending %s request: %s %s %s %s",
                self._requests_mode.name,
                method,
                url,
                payload,
                self._redact_headers(),
            )

            conn.request("POST", url_path, payload, headers)
            resp = conn.getresponse()
            log.debug("Response status: %s", resp.status)
            response = Response.from_http_response(resp)
            return response
        finally:
            if conn is not None:
                conn.close()

    def _do_request_with_telemetry(
        self,
        method: str,
        endpoint: str,
        payload: str,
        metric_names: APIRequestMetricNames,
        timeout: t.Optional[float] = None,
    ) -> t.Any:
        """Performs a request with telemetry submitted according to given names"""
        sw = StopWatch()
        sw.start()
        error_type: t.Optional[ERROR_TYPES] = None
        response_bytes: t.Optional[int] = None
        try:
            try:
                response = self._do_request(method, endpoint, payload, timeout=timeout)
            except _NETWORK_ERRORS:
                error_type = ERROR_TYPES.TIMEOUT
                raise
            if response.status >= 400:
                error_type = ERROR_TYPES.CODE_4XX if response.status < 500 else ERROR_TYPES.CODE_5XX
                if response.status == 403:
                    raise CIVisibilityAuthenticationException()
                raise ValueError("API response status code: %d", response.status)
            try:
                sw.stop()  # Stop the timer before parsing the response
                response_bytes = len(response.body)
                parsed = json.loads(response.body)
                return parsed
            except JSONDecodeError:
                error_type = ERROR_TYPES.BAD_JSON
                raise
        finally:
            record_api_request(metric_names, sw.elapsed() * 1000, response_bytes=response_bytes, error=error_type)

    def fetch_settings(self) -> TestVisibilityAPISettings:
        """Fetches settings from the test visibility API endpoint

        This raises encountered exceptions because fetch_settings may be used multiple times during a session.
        """

        metric_names = APIRequestMetricNames(
            count=GIT_TELEMETRY.SETTINGS_COUNT.value,
            duration=GIT_TELEMETRY.SETTINGS_MS.value,
            response_bytes=None,
            error=GIT_TELEMETRY.SETTINGS_ERRORS.value,
        )

        payload = {
            "data": {
                "id": str(uuid4()),
                "type": "ci_app_test_service_libraries_settings",
                "attributes": {
                    "test_level": TEST if self._itr_skipping_level == ITR_SKIPPING_LEVEL.TEST else SUITE,
                    "service": self._service,
                    "env": self._dd_env,
                    "repository_url": self._git_data.repository_url,
                    "sha": self._git_data.commit_sha,
                    "branch": self._git_data.branch,
                    "configurations": self._configurations,
                },
            }
        }

        parsed_response = self._do_request_with_telemetry(
            "POST", SETTING_ENDPOINT, json.dumps(payload), metric_names, timeout=self._timeout
        )

        if "errors" in parsed_response:
            record_api_request_error(metric_names.error, ERROR_TYPES.UNKNOWN)
            raise ValueError("Settings response contained an error, disabling Intelligent Test Runner")

        log.debug("Parsed API response: %s", parsed_response)

        try:
            attributes = parsed_response["data"]["attributes"]
            coverage_enabled = attributes["code_coverage"]
            skipping_enabled = attributes["tests_skipping"]
            require_git = attributes["require_git"]
            itr_enabled = attributes["itr_enabled"]
            flaky_test_retries_enabled = attributes["flaky_test_retries_enabled"] or asbool(
                os.getenv("_DD_TEST_FORCE_ENABLE_ATR")
            )
            known_tests_enabled = attributes["known_tests_enabled"]

            if attributes["early_flake_detection"]["enabled"]:
                early_flake_detection = EarlyFlakeDetectionSettings(
                    enabled=attributes["early_flake_detection"]["enabled"],
                    slow_test_retries_5s=attributes["early_flake_detection"]["slow_test_retries"]["5s"],
                    slow_test_retries_10s=attributes["early_flake_detection"]["slow_test_retries"]["10s"],
                    slow_test_retries_30s=attributes["early_flake_detection"]["slow_test_retries"]["30s"],
                    slow_test_retries_5m=attributes["early_flake_detection"]["slow_test_retries"]["5m"],
                    faulty_session_threshold=attributes["early_flake_detection"]["faulty_session_threshold"],
                )
            else:
                early_flake_detection = EarlyFlakeDetectionSettings()

            test_management_attributes = attributes.get("test_management", {})
            test_management_enabled = test_management_attributes.get("enabled", False)
            attempt_to_fix_retries_env = os.getenv("DD_TEST_MANAGEMENT_ATTEMPT_TO_FIX_RETRIES")
            if attempt_to_fix_retries_env and attempt_to_fix_retries_env.isdigit():
                attempt_to_fix_retries = int(attempt_to_fix_retries_env)
                log.debug("Number of Attempt to Fix retries obtained from environment: %d", attempt_to_fix_retries)
            else:
                attempt_to_fix_retries = test_management_attributes.get(
                    "attempt_to_fix_retries", DEFAULT_ATTEMPT_TO_FIX_RETRIES
                )
                log.debug("Number of Attempt to Fix retries obtained from API: %d", attempt_to_fix_retries)

            test_management = TestManagementSettings(
                enabled=test_management_enabled or asbool(os.getenv("_DD_TEST_FORCE_ENABLE_TEST_MANAGEMENT")),
                attempt_to_fix_retries=attempt_to_fix_retries,
            )

        except KeyError:
            record_api_request_error(metric_names.error, ERROR_TYPES.UNKNOWN)
            raise

        api_settings = TestVisibilityAPISettings(
            coverage_enabled=coverage_enabled,
            skipping_enabled=skipping_enabled,
            require_git=require_git,
            itr_enabled=itr_enabled,
            flaky_test_retries_enabled=flaky_test_retries_enabled,
            known_tests_enabled=known_tests_enabled,
            early_flake_detection=early_flake_detection,
            test_management=test_management,
        )

        record_settings_response(
            coverage_enabled=api_settings.coverage_enabled,
            skipping_enabled=api_settings.skipping_enabled,
            require_git=api_settings.require_git,
            itr_enabled=api_settings.itr_enabled,
            flaky_test_retries_enabled=api_settings.flaky_test_retries_enabled,
            known_tests_enabled=api_settings.known_tests_enabled,
            early_flake_detection_enabled=api_settings.early_flake_detection.enabled,
            test_management_enabled=api_settings.test_management.enabled,
        )

        return api_settings

    def fetch_skippable_items(
        self, timeout: t.Optional[float] = None, ignore_test_parameters: bool = False
    ) -> t.Optional[ITRData]:
        if timeout is None:
            timeout = DEFAULT_ITR_SKIPPABLE_TIMEOUT

        metric_names = APIRequestMetricNames(
            count=SKIPPABLE_TESTS_TELEMETRY.REQUEST.value,
            duration=SKIPPABLE_TESTS_TELEMETRY.REQUEST_MS.value,
            response_bytes=SKIPPABLE_TESTS_TELEMETRY.RESPONSE_BYTES.value,
            error=SKIPPABLE_TESTS_TELEMETRY.REQUEST_ERRORS.value,
        )

        payload = {
            "data": {
                "id": str(uuid4()),
                "type": "test_params",
                "attributes": {
                    "service": self._service,
                    "env": self._dd_env,
                    "repository_url": self._git_data.repository_url,
                    "sha": self._git_data.commit_sha,
                    "configurations": self._configurations,
                    "test_level": TEST if self._itr_skipping_level == ITR_SKIPPING_LEVEL.TEST else SUITE,
                },
            }
        }

        try:
            skippable_response: _SkippableResponse = self._do_request_with_telemetry(
                "POST", SKIPPABLE_ENDPOINT, json.dumps(payload), metric_names, timeout
            )
        except Exception:  # noqa: E722
            return None

        covered_files: t.Optional[t.Dict[str, CoverageLines]] = None

        if skippable_response is None:
            # We did not fetch any data, but telemetry has already been recorded, and a warning has been logged
            return None

        meta = skippable_response.get("meta")
        if meta is None:
            log.debug("Skippable tests response did not contain metadata field, no tests will be skipped")
            record_api_request_error(metric_names.error, ERROR_TYPES.BAD_JSON)
            return None

        correlation_id = meta.get("correlation_id")
        if correlation_id is None:
            log.debug("Skippable tests response missing correlation_id")
        else:
            log.debug("Skippable tests response correlation_id: %s", correlation_id)

        covered_files_data = meta.get("coverage")
        if covered_files_data is not None:
            covered_files = _parse_covered_files(covered_files_data)

        items_to_skip_data = skippable_response.get("data")
        if items_to_skip_data is None:
            log.warning("Skippable tests request missing data, no tests will be skipped")
            record_api_request_error(metric_names.error, ERROR_TYPES.BAD_JSON)
            return None

        if self._itr_skipping_level == ITR_SKIPPING_LEVEL.TEST:
            items_to_skip = _parse_skippable_tests(items_to_skip_data, ignore_test_parameters)
        else:
            items_to_skip = _parse_skippable_suites(items_to_skip_data)
        return ITRData(
            correlation_id=correlation_id,
            covered_files=covered_files,
            skippable_items=items_to_skip,
        )

    def fetch_known_tests(self) -> t.Optional[t.Set[TestId]]:
        metric_names = APIRequestMetricNames(
            count=EARLY_FLAKE_DETECTION_TELEMETRY.REQUEST.value,
            duration=EARLY_FLAKE_DETECTION_TELEMETRY.REQUEST_MS.value,
            response_bytes=EARLY_FLAKE_DETECTION_TELEMETRY.RESPONSE_BYTES.value,
            error=EARLY_FLAKE_DETECTION_TELEMETRY.REQUEST_ERRORS.value,
        )

        known_test_ids: t.Set[TestId] = set()

        payload = {
            "data": {
                "id": str(uuid4()),
                "type": "ci_app_libraries_tests_request",
                "attributes": {
                    "service": self._service,
                    "env": self._dd_env,
                    "repository_url": self._git_data.repository_url,
                    "configurations": self._configurations,
                },
            }
        }

        try:
            parsed_response = self._do_request_with_telemetry(
                "POST", KNOWN_TESTS_ENDPOINT, json.dumps(payload), metric_names
            )
        except Exception:  # noqa: E722
            return None

        if "errors" in parsed_response:
            record_api_request_error(metric_names.error, ERROR_TYPES.UNKNOWN)
            log.debug("Unique tests response contained an error")
            return None

        try:
            tests_data = parsed_response["data"]["attributes"]["tests"]
        except KeyError:
            record_api_request_error(metric_names.error, ERROR_TYPES.UNKNOWN)
            return None

        try:
            for module, suites in tests_data.items():
                module_id = TestModuleId(module)
                for suite, tests in suites.items():
                    suite_id = TestSuiteId(module_id, suite)
                    for test in tests:
                        known_test_ids.add(TestId(suite_id, test))
        except Exception:  # noqa: E722
            log.debug("Failed to parse unique tests data", exc_info=True)
            record_api_request_error(metric_names.error, ERROR_TYPES.UNKNOWN)
            return None

        record_early_flake_detection_tests_count(len(known_test_ids))

        return known_test_ids

    def fetch_test_management_tests(self) -> t.Optional[t.Dict[TestId, TestProperties]]:
        metric_names = APIRequestMetricNames(
            count=TEST_MANAGEMENT_TELEMETRY.REQUEST.value,
            duration=TEST_MANAGEMENT_TELEMETRY.REQUEST_MS.value,
            response_bytes=TEST_MANAGEMENT_TELEMETRY.RESPONSE_BYTES.value,
            error=TEST_MANAGEMENT_TELEMETRY.REQUEST_ERRORS.value,
        )

        test_properties: t.Dict[TestId, TestProperties] = {}
        payload = {
            "data": {
                "id": str(uuid4()),
                "type": "ci_app_libraries_tests_request",
                "attributes": {
                    "repository_url": self._git_data.repository_url,
                    "commit_message": self._git_data.commit_message,
                    "sha": self._git_data.commit_sha,
                },
            }
        }

        try:
            parsed_response = self._do_request_with_telemetry(
                "POST", TEST_MANAGEMENT_TESTS_ENDPOINT, json.dumps(payload), metric_names
            )
        except Exception:  # noqa: E722
            return None

        if "errors" in parsed_response:
            record_api_request_error(metric_names.error, ERROR_TYPES.UNKNOWN)
            log.debug("Test Management tests response contained an error")
            return None

        try:
            modules = parsed_response["data"]["attributes"]["modules"]
        except KeyError:
            record_api_request_error(metric_names.error, ERROR_TYPES.UNKNOWN)
            return None

        try:
            for module_name, module_data in modules.items():
                module_id = TestModuleId(module_name)
                suites = module_data["suites"]
                for suite_name, suite_data in suites.items():
                    suite_id = TestSuiteId(module_id, suite_name)
                    tests = suite_data["tests"]
                    for test_name, test_data in tests.items():
                        test_id = TestId(suite_id, test_name)
                        properties = test_data.get("properties", {})
                        test_properties[test_id] = TestProperties(
                            quarantined=properties.get("quarantined", False),
                            disabled=properties.get("disabled", False),
                            attempt_to_fix=properties.get("attempt_to_fix", False),
                        )

        except Exception:  # noqa: E722
            log.debug("Failed to parse Test Management tests data", exc_info=True)
            record_api_request_error(metric_names.error, ERROR_TYPES.UNKNOWN)
            return None

        record_test_management_tests_count(len(test_properties))

        return test_properties


class AgentlessTestVisibilityAPIClient(_TestVisibilityAPIClientBase):
    _requests_mode = REQUESTS_MODE.AGENTLESS_EVENTS

    def __init__(
        self,
        itr_skipping_level: ITR_SKIPPING_LEVEL,
        git_data: GitData,
        configurations: _CONFIGURATIONS_TYPE,
        api_key: str,
        dd_site: t.Optional[str] = None,
        agentless_url: t.Optional[str] = None,
        dd_service: t.Optional[str] = None,
        dd_env: t.Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        if not api_key:
            raise ValueError("API key is required for AgentlessTestVisibilityAPIClient")

        _dd_site = dd_site if dd_site is not None else AGENTLESS_DEFAULT_SITE
        base_url = agentless_url if agentless_url is not None else "https://api." + _dd_site

        super().__init__(base_url, itr_skipping_level, git_data, configurations, dd_service, dd_env, timeout)
        self._api_key = api_key

    def _get_headers(self):
        return {AGENTLESS_API_KEY_HEADER_NAME: self._api_key}

    def _redact_headers(self) -> t.Dict[str, str]:
        """Sanitize headers for logging"""
        headers = self._get_final_headers()
        headers[AGENTLESS_API_KEY_HEADER_NAME] = "REDACTED"
        return headers


class EVPProxyTestVisibilityAPIClient(_TestVisibilityAPIClientBase):
    _requests_mode = REQUESTS_MODE.EVP_PROXY_EVENTS

    def __init__(
        self,
        itr_skipping_level: ITR_SKIPPING_LEVEL,
        git_data: GitData,
        configurations: _CONFIGURATIONS_TYPE,
        agent_url: str,
        dd_service: t.Optional[str] = None,
        dd_env: t.Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
        evp_proxy_base_url: str = EVP_PROXY_AGENT_BASE_PATH,
    ):
        base_url = combine_url_path(agent_url, evp_proxy_base_url)
        super().__init__(base_url, itr_skipping_level, git_data, configurations, dd_service, dd_env, timeout)

    def _get_headers(self):
        return {EVP_SUBDOMAIN_HEADER_NAME: EVP_SUBDOMAIN_HEADER_API_VALUE}

    def _redact_headers(self) -> t.Dict[str, str]:
        """EVP proxy headers do not include authentication information"""
        return self._get_final_headers()
