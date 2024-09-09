from collections import defaultdict
from http.client import RemoteDisconnected
import json
import os
from pathlib import Path
import re
import socket
from typing import TYPE_CHECKING  # noqa:F401
from typing import Any  # noqa:F401
from typing import NamedTuple  # noqa:F401
from typing import Optional
from typing import Union  # noqa:F401
from uuid import uuid4

import ddtrace
from ddtrace import Tracer
from ddtrace import config as ddconfig
from ddtrace.contrib import trace_utils
from ddtrace.ext import ci
from ddtrace.ext import test
from ddtrace.ext.ci_visibility._ci_visibility_base import CIItemId
from ddtrace.ext.ci_visibility._ci_visibility_base import _CISessionId
from ddtrace.ext.ci_visibility.api import CIBase
from ddtrace.ext.ci_visibility.api import CIITRMixin
from ddtrace.ext.ci_visibility.api import CIModule
from ddtrace.ext.ci_visibility.api import CIModuleId
from ddtrace.ext.ci_visibility.api import CISession
from ddtrace.ext.ci_visibility.api import CISuite
from ddtrace.ext.ci_visibility.api import CISuiteId
from ddtrace.ext.ci_visibility.api import CITest
from ddtrace.ext.ci_visibility.api import CITestId
from ddtrace.internal import atexit
from ddtrace.internal import compat
from ddtrace.internal import core
from ddtrace.internal import telemetry
from ddtrace.internal.agent import get_connection
from ddtrace.internal.agent import get_trace_url
from ddtrace.internal.ci_visibility.coverage import is_coverage_available
from ddtrace.internal.ci_visibility.filters import TraceCiVisibilityFilter
from ddtrace.internal.codeowners import Codeowners
from ddtrace.internal.compat import JSONDecodeError
from ddtrace.internal.compat import parse
from ddtrace.internal.logger import get_logger
from ddtrace.internal.service import Service
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.writer.writer import Response

from .. import agent
from ..utils.http import verify_url
from ..utils.time import StopWatch
from .api.ci_module import CIVisibilityModule
from .api.ci_session import CIVisibilitySession
from .api.ci_session import CIVisibilitySessionSettings
from .api.ci_suite import CIVisibilitySuite
from .api.ci_test import CIVisibilityTest
from .constants import AGENTLESS_API_KEY_HEADER_NAME
from .constants import AGENTLESS_DEFAULT_SITE
from .constants import CUSTOM_CONFIGURATIONS_PREFIX
from .constants import EVP_PROXY_AGENT_BASE_PATH
from .constants import EVP_SUBDOMAIN_HEADER_API_VALUE
from .constants import EVP_SUBDOMAIN_HEADER_EVENT_VALUE
from .constants import EVP_SUBDOMAIN_HEADER_NAME
from .constants import ITR_CORRELATION_ID_TAG_NAME
from .constants import REQUESTS_MODE
from .constants import SETTING_ENDPOINT
from .constants import SKIPPABLE_ENDPOINT
from .constants import SUITE
from .constants import TEST
from .constants import TRACER_PARTIAL_FLUSH_MIN_SPANS
from .context import CIContextProvider
from .errors import CIVisibilityDataError
from .errors import CIVisibilityError
from .git_client import METADATA_UPLOAD_STATUS
from .git_client import CIVisibilityGitClient
from .telemetry.constants import ERROR_TYPES
from .telemetry.constants import TEST_FRAMEWORKS
from .telemetry.git import record_settings
from .telemetry.itr import record_itr_skippable_request
from .writer import CIVisibilityWriter


if TYPE_CHECKING:  # pragma: no cover
    from typing import DefaultDict  # noqa:F401
    from typing import Dict  # noqa:F401
    from typing import List  # noqa:F401
    from typing import Tuple  # noqa:F401

    from ddtrace.settings import IntegrationConfig  # noqa:F401

log = get_logger(__name__)

DEFAULT_TIMEOUT = 15
DEFAULT_ITR_SKIPPABLE_TIMEOUT = 20

_CIVisibilitySettings = NamedTuple(
    "_CIVisibilitySettings",
    [("coverage_enabled", bool), ("skipping_enabled", bool), ("require_git", bool), ("itr_enabled", bool)],
)


class CIVisibilityAuthenticationException(Exception):
    pass


def _extract_repository_name_from_url(repository_url: str) -> str:
    _REPO_NAME_REGEX = r".*/(?P<repo_name>.*?)(\.git)?$"

    try:
        url_path = parse.urlparse(repository_url).path
        matches = re.match(_REPO_NAME_REGEX, url_path, flags=re.IGNORECASE)
        if matches:
            return matches.group("repo_name")
        log.warning("Cannot extract repository name from unexpected URL path: %s", url_path)
        return repository_url
    except ValueError:
        log.warning("Repository name cannot be parsed from repository_url: %s", repository_url)
        return repository_url


def _get_git_repo():
    # this exists only for the purpose of patching in tests
    return None


def _get_custom_configurations():
    # type () -> dict
    custom_configurations = {}
    for tag, value in ddconfig.tags.items():
        if tag.startswith(CUSTOM_CONFIGURATIONS_PREFIX):
            custom_configurations[tag.replace("%s." % CUSTOM_CONFIGURATIONS_PREFIX, "", 1)] = value

    return custom_configurations


def _do_request(method, url, payload, headers, timeout=DEFAULT_TIMEOUT):
    # type: (str, str, str, Dict, int) -> Response
    try:
        parsed_url = verify_url(url)
        url_path = parsed_url.path
        conn = get_connection(url, timeout=timeout)
        log.debug("Sending request: %s %s %s %s", method, url_path, payload, headers)
        conn.request("POST", url_path, payload, headers)
        resp = compat.get_connection_response(conn)
        log.debug("Response status: %s", resp.status)
        result = Response.from_http_response(resp)
    finally:
        conn.close()
    return result


class CIVisibility(Service):
    _instance = None  # type: Optional[CIVisibility]
    enabled = False
    _test_suites_to_skip = None  # type: Optional[List[str]]
    _tests_to_skip = defaultdict(list)  # type: DefaultDict[str, List[str]]

    def __init__(self, tracer=None, config=None, service=None):
        # type: (Optional[Tracer], Optional[IntegrationConfig], Optional[str]) -> None
        super(CIVisibility, self).__init__()

        if tracer:
            self.tracer = tracer
        else:
            if asbool(os.getenv("_DD_CIVISIBILITY_USE_CI_CONTEXT_PROVIDER")):
                # Create a new CI tracer
                self.tracer = Tracer(context_provider=CIContextProvider())
            else:
                self.tracer = ddtrace.tracer

            # Partial traces are required for ITR to work in suite-level skipping for long test sessions, but we
            # assume that a tracer is already configured if it's been passed in.
            self.tracer.configure(partial_flush_enabled=True, partial_flush_min_spans=TRACER_PARTIAL_FLUSH_MIN_SPANS)

        self._configurations = ci._get_runtime_and_os_metadata()
        custom_configurations = _get_custom_configurations()
        if custom_configurations:
            self._configurations["custom"] = custom_configurations

        self._api_key = os.getenv("_CI_DD_API_KEY", os.getenv("DD_API_KEY"))

        self._dd_site = os.getenv("DD_SITE", AGENTLESS_DEFAULT_SITE)
        self._suite_skipping_mode = asbool(os.getenv("_DD_CIVISIBILITY_ITR_SUITE_MODE", default=False))
        self.config = config or ddconfig.ci_visibility  # type: Optional[IntegrationConfig]
        self._tags = ci.tags(cwd=_get_git_repo())  # type: Dict[str, str]
        self._service = service
        self._codeowners = None
        self._root_dir = None
        self._should_upload_git_metadata = True
        self._itr_meta = {}  # type: Dict[str, Any]

        self._session: Optional[CIVisibilitySession] = None

        if service is None:
            # Use service if provided to enable() or __init__()
            int_service = None
            if self.config is not None:
                int_service = trace_utils.int_service(None, self.config)
            # check if repository URL detected from environment or .git, and service name unchanged
            if (
                self._tags.get(ci.git.REPOSITORY_URL, None)
                and self.config
                and int_service == self.config._default_service
            ):
                self._service = _extract_repository_name_from_url(self._tags[ci.git.REPOSITORY_URL])
            elif self._service is None and int_service is not None:
                self._service = int_service

        if ddconfig._ci_visibility_agentless_enabled:
            if not self._api_key:
                raise EnvironmentError(
                    "DD_CIVISIBILITY_AGENTLESS_ENABLED is set, but DD_API_KEY is not set, so ddtrace "
                    "cannot be initialized."
                )
            requests_mode_str = "agentless"
            self._requests_mode = REQUESTS_MODE.AGENTLESS_EVENTS
        elif self._agent_evp_proxy_is_available():
            requests_mode_str = "agent EVP proxy"
            self._requests_mode = REQUESTS_MODE.EVP_PROXY_EVENTS
        else:
            requests_mode_str = "APM (some features will be disabled"
            self._requests_mode = REQUESTS_MODE.TRACES
            self._should_upload_git_metadata = False

        if self._should_upload_git_metadata:
            self._git_client = CIVisibilityGitClient(api_key=self._api_key or "", requests_mode=self._requests_mode)
            self._git_client.upload_git_metadata(cwd=_get_git_repo())

        self._api_settings = self._check_enabled_features()

        self._collect_coverage_enabled = self._should_collect_coverage(self._api_settings.coverage_enabled)

        self._configure_writer(coverage_enabled=self._collect_coverage_enabled)

        log.info("Service: %s (env: %s)", self._service, ddconfig.env)
        log.info("Requests mode: %s", requests_mode_str)
        log.info("Git metadata upload enabled: %s", self._should_upload_git_metadata)
        log.info("API-provided settings: coverage collection: %s", self._api_settings.coverage_enabled)
        log.info(
            "API-provided settings: Intelligent Test Runner: %s, test skipping: %s",
            self._api_settings.itr_enabled,
            self._api_settings.skipping_enabled,
        )
        log.info("Detected configurations: %s", str(self._configurations))

        try:
            self._codeowners = Codeowners()
        except ValueError:
            log.warning("CODEOWNERS file is not available")
        except Exception:
            log.warning("Failed to load CODEOWNERS", exc_info=True)

    @staticmethod
    def _should_collect_coverage(coverage_enabled_by_api):
        if not coverage_enabled_by_api and not asbool(
            os.getenv("_DD_CIVISIBILITY_ITR_FORCE_ENABLE_COVERAGE", default=False)
        ):
            return False
        if not is_coverage_available():
            log.warning(
                "CI Visibility code coverage tracking is enabled, but the `coverage` package is not installed."
                "To use code coverage tracking, please install `coverage` from https://pypi.org/project/coverage/"
            )
            return False
        return True

    def _check_settings_api(self, url, headers):
        # type: (str, Dict[str, str]) -> _CIVisibilitySettings
        payload = {
            "data": {
                "id": str(uuid4()),
                "type": "ci_app_test_service_libraries_settings",
                "attributes": {
                    "test_level": SUITE if self._suite_skipping_mode else TEST,
                    "service": self._service,
                    "env": ddconfig.env,
                    "repository_url": self._tags.get(ci.git.REPOSITORY_URL),
                    "sha": self._tags.get(ci.git.COMMIT_SHA),
                    "branch": self._tags.get(ci.git.BRANCH),
                    "configurations": self._configurations,
                },
            }
        }

        sw = StopWatch()
        sw.start()
        try:
            response = _do_request("POST", url, json.dumps(payload), headers)
        except TimeoutError:
            record_settings(sw.elapsed() * 1000, error=ERROR_TYPES.TIMEOUT)
            raise
        if response.status >= 400:
            error_code = ERROR_TYPES.CODE_4XX if response.status < 500 else ERROR_TYPES.CODE_5XX
            record_settings(sw.elapsed() * 1000, error=error_code)
            if response.status == 403:
                raise CIVisibilityAuthenticationException()
            raise ValueError("API response status code: %d", response.status)
        try:
            if isinstance(response.body, bytes):
                parsed = json.loads(response.body.decode())
            else:
                parsed = json.loads(response.body)
        except JSONDecodeError:
            record_settings(sw.elapsed() * 1000, error=ERROR_TYPES.BAD_JSON)
            raise

        if "errors" in parsed and parsed["errors"][0] == "Not found":
            record_settings(sw.elapsed() * 1000, error=ERROR_TYPES.UNKNOWN)
            raise ValueError("Settings response contained an error, disabling Intelligent Test Runner")

        log.debug("Parsed API response: %s", parsed)

        try:
            attributes = parsed["data"]["attributes"]
            coverage_enabled = attributes["code_coverage"]
            skipping_enabled = attributes["tests_skipping"]
            require_git = attributes["require_git"]
            itr_enabled = attributes.get("itr_enabled", False)
        except KeyError:
            record_settings(sw.elapsed() * 1000, error=ERROR_TYPES.UNKNOWN)
            raise

        record_settings(sw.elapsed() * 1000, coverage_enabled, skipping_enabled, require_git, itr_enabled)

        return _CIVisibilitySettings(coverage_enabled, skipping_enabled, require_git, itr_enabled)

    def _check_enabled_features(self):
        # type: () -> _CIVisibilitySettings
        # DEV: Remove this ``if`` once ITR is in GA
        _error_return_value = _CIVisibilitySettings(False, False, False, False)

        if not ddconfig._ci_visibility_intelligent_testrunner_enabled:
            return _error_return_value

        if self._requests_mode == REQUESTS_MODE.EVP_PROXY_EVENTS:
            url = get_trace_url() + EVP_PROXY_AGENT_BASE_PATH + SETTING_ENDPOINT
            _headers = {
                EVP_SUBDOMAIN_HEADER_NAME: EVP_SUBDOMAIN_HEADER_API_VALUE,
            }
            log.debug("Making EVP request to agent: url=%s, headers=%s", url, _headers)
        elif self._requests_mode == REQUESTS_MODE.AGENTLESS_EVENTS:
            if not self._api_key:
                log.debug("Cannot make request to setting endpoint if API key is not set")
                return _error_return_value
            url = "https://api." + self._dd_site + SETTING_ENDPOINT
            if ddconfig._ci_visibility_agentless_url:
                url = ddconfig._ci_visibility_agentless_url + SETTING_ENDPOINT
            _headers = {
                AGENTLESS_API_KEY_HEADER_NAME: self._api_key,
                "Content-Type": "application/json",
            }
        else:
            log.warning("Cannot make requests to setting endpoint if mode is not agentless or evp proxy")
            return _error_return_value

        try:
            settings = self._check_settings_api(url, _headers)
        except CIVisibilityAuthenticationException:
            # Authentication exception is handled during enable() to prevent the service from being used
            raise
        except Exception:
            log.warning(
                "Error checking Intelligent Test Runner API, disabling coverage collection and test skipping",
                exc_info=True,
            )
            return _error_return_value

        if settings.require_git:
            log.info("Settings API requires git metadata, waiting for git metadata upload to complete")
            try:
                try:
                    if self._git_client.wait_for_metadata_upload_status() == METADATA_UPLOAD_STATUS.FAILED:
                        log.warning("Metadata upload failed, test skipping will be best effort")
                except ValueError:
                    log.warning(
                        "Error waiting for git metadata upload, test skipping will be best effort", exc_info=True
                    )
            except TimeoutError:
                log.warning("Timeout waiting for metadata upload, test skipping will be best effort")

            # The most recent API response overrides the first one
            try:
                settings = self._check_settings_api(url, _headers)
            except Exception:
                log.warning(
                    "Error checking Intelligent Test Runner API after git metadata upload,"
                    " disabling coverage and test skipping",
                    exc_info=True,
                )
                return _error_return_value
            if settings.require_git:
                log.warning("git metadata upload did not complete in time, test skipping will be best effort")

        return settings

    def _configure_writer(self, coverage_enabled=False, requests_mode=None):
        writer = None
        if requests_mode is None:
            requests_mode = self._requests_mode

        if requests_mode == REQUESTS_MODE.AGENTLESS_EVENTS:
            headers = {"dd-api-key": self._api_key}
            writer = CIVisibilityWriter(
                headers=headers,
                coverage_enabled=coverage_enabled,
                itr_suite_skipping_mode=self._suite_skipping_mode,
            )
        elif requests_mode == REQUESTS_MODE.EVP_PROXY_EVENTS:
            writer = CIVisibilityWriter(
                intake_url=agent.get_trace_url(),
                headers={EVP_SUBDOMAIN_HEADER_NAME: EVP_SUBDOMAIN_HEADER_EVENT_VALUE},
                use_evp=True,
                coverage_enabled=coverage_enabled,
                itr_suite_skipping_mode=self._suite_skipping_mode,
            )
        if writer is not None:
            self.tracer.configure(writer=writer)

    def _agent_evp_proxy_is_available(self):
        # type: () -> bool
        try:
            info = agent.info()
        except Exception:
            info = None

        if info:
            endpoints = info.get("endpoints", [])
            if endpoints and any(EVP_PROXY_AGENT_BASE_PATH in endpoint for endpoint in endpoints):
                return True
        return False

    @classmethod
    def is_itr_enabled(cls):
        # cls.enabled guarantees _instance is not None
        return cls.enabled and cls._instance._api_settings.itr_enabled

    @classmethod
    def test_skipping_enabled(cls):
        if not cls.enabled or asbool(os.getenv("_DD_CIVISIBILITY_ITR_PREVENT_TEST_SKIPPING", default=False)):
            return False
        return cls._instance and cls._instance._api_settings.skipping_enabled

    @classmethod
    def should_collect_coverage(cls):
        return cls._instance._api_settings.coverage_enabled or asbool(
            os.getenv("_DD_CIVISIBILITY_ITR_FORCE_ENABLE_COVERAGE", default=False)
        )

    def _fetch_tests_to_skip(self, skipping_mode: str):
        # Make sure git uploading has finished
        # this will block the thread until that happens
        try:
            try:
                metadata_upload_status = self._git_client.wait_for_metadata_upload_status()
                if metadata_upload_status not in [METADATA_UPLOAD_STATUS.SUCCESS, METADATA_UPLOAD_STATUS.UNNECESSARY]:
                    log.warning("git metadata upload was not successful, some tests may not be skipped")
            except ValueError:
                log.warning(
                    "Error waiting for metadata upload to complete while fetching tests to skip"
                    ", some tests may not be skipped",
                    exc_info=True,
                )
        except TimeoutError:
            log.debug("Timed out waiting for git metadata upload, some tests may not be skipped")

        payload = {
            "data": {
                "type": "test_params",
                "attributes": {
                    "service": self._service,
                    "env": ddconfig.env,
                    "repository_url": self._tags.get(ci.git.REPOSITORY_URL),
                    "sha": self._tags.get(ci.git.COMMIT_SHA),
                    "configurations": self._configurations,
                    "test_level": skipping_mode,
                },
            }
        }

        _headers = {
            "dd-api-key": self._api_key,
            "Content-Type": "application/json",
        }

        if self._requests_mode == REQUESTS_MODE.EVP_PROXY_EVENTS:
            url = get_trace_url() + EVP_PROXY_AGENT_BASE_PATH + SKIPPABLE_ENDPOINT
            _headers = {
                EVP_SUBDOMAIN_HEADER_NAME: EVP_SUBDOMAIN_HEADER_API_VALUE,
            }
        elif self._requests_mode == REQUESTS_MODE.AGENTLESS_EVENTS:
            url = "https://api." + self._dd_site + SKIPPABLE_ENDPOINT
            if ddconfig._ci_visibility_agentless_url:
                url = ddconfig._ci_visibility_agentless_url + SKIPPABLE_ENDPOINT
        else:
            log.warning("Cannot make requests to skippable endpoint if mode is not agentless or evp proxy")
            return

        error_type: Optional[ERROR_TYPES] = None
        response_bytes: int = 0
        skippable_count: int = 0
        sw = StopWatch()

        try:
            try:
                sw.start()
                response = _do_request("POST", url, json.dumps(payload), _headers, DEFAULT_ITR_SKIPPABLE_TIMEOUT)
                sw.stop()
            except (TimeoutError, socket.timeout, RemoteDisconnected) as e:
                sw.stop()
                log.warning("Error while fetching skippable tests: ", exc_info=True)
                error_type = ERROR_TYPES.NETWORK if isinstance(e, RemoteDisconnected) else ERROR_TYPES.TIMEOUT
                self._test_suites_to_skip = []
                return

            self._test_suites_to_skip = []

            if response.status >= 400:
                error_type = ERROR_TYPES.CODE_4XX if response.status < 500 else ERROR_TYPES.CODE_5XX
                log.warning("Skippable tests request responded with status %d", response.status)
                return
            try:
                response_bytes = len(response.body)
                if isinstance(response.body, bytes):
                    parsed = json.loads(response.body.decode())
                else:
                    parsed = json.loads(response.body)
            except json.JSONDecodeError:
                log.warning("Skippable tests request responded with invalid JSON '%s'", response.body)
                error_type = ERROR_TYPES.BAD_JSON
                return

            if "data" not in parsed:
                log.warning("Skippable tests request missing data, no tests will be skipped")
                error_type = ERROR_TYPES.BAD_JSON
                return

            if "meta" in parsed and "correlation_id" in parsed["meta"]:
                itr_correlation_id = parsed["meta"]["correlation_id"]
                log.debug("Skippable tests response correlation_id: %s", itr_correlation_id)
                self._itr_meta[ITR_CORRELATION_ID_TAG_NAME] = itr_correlation_id
            else:
                log.debug("Skippable tests response missing correlation_id")

            try:
                for item in parsed["data"]:
                    if item["type"] == skipping_mode and "suite" in item["attributes"]:
                        module = item["attributes"].get("configurations", {}).get("test.bundle", "").replace(".", "/")
                        path = (
                            "/".join((module, item["attributes"]["suite"])) if module else item["attributes"]["suite"]
                        )
                        skippable_count += 1

                        if skipping_mode == SUITE:
                            self._test_suites_to_skip.append(path)
                        else:
                            self._tests_to_skip[path].append(item["attributes"]["name"])
            except Exception:
                log.warning("Error processing skippable test data, no tests will be skipped", exc_info=True)
                error_type = ERROR_TYPES.UNKNOWN
                self._test_suites_to_skip = []
                self._tests_to_skip = defaultdict(list)

        except Exception:
            log.warning("Error retrieving skippable test data, no tests will be skipped", exc_info=True)
            error_type = ERROR_TYPES.UNKNOWN
            self._test_suites_to_skip = []
            self._tests_to_skip = defaultdict(list)

        finally:
            record_itr_skippable_request(
                sw.elapsed() * 1000,
                response_bytes,
                skipping_mode,
                skippable_count if error_type is None else None,
                error_type,
            )

    def _should_skip_path(self, path, name, test_skipping_mode=None):
        if test_skipping_mode is None:
            _test_skipping_mode = SUITE if self._suite_skipping_mode else TEST
        else:
            _test_skipping_mode = test_skipping_mode

        if _test_skipping_mode == SUITE:
            return os.path.relpath(path) in self._test_suites_to_skip
        else:
            return name in self._tests_to_skip[os.path.relpath(path)]
        return False

    @classmethod
    def enable(cls, tracer=None, config=None, service=None):
        # type: (Optional[Tracer], Optional[Any], Optional[str]) -> None
        log.debug("Enabling %s", cls.__name__)
        if ddconfig._ci_visibility_agentless_enabled:
            if not os.getenv("_CI_DD_API_KEY", os.getenv("DD_API_KEY")):
                log.critical(
                    "%s disabled: environment variable DD_CIVISIBILITY_AGENTLESS_ENABLED is true but"
                    " DD_API_KEY is not set",
                    cls.__name__,
                )
                cls.enabled = False
                return

        if cls._instance is not None:
            log.debug("%s already enabled", cls.__name__)
            return

        _register_session_handlers()

        try:
            cls._instance = cls(tracer=tracer, config=config, service=service)
        except CIVisibilityAuthenticationException:
            log.warning("Authentication error, disabling CI Visibility, please check Datadog API key")
            cls.enabled = False
            return

        cls.enabled = True

        cls._instance.start()
        atexit.register(cls.disable)

        log.debug("%s enabled", cls.__name__)
        log.info(
            "Final settings: coverage collection: %s, test skipping: %s",
            cls._instance._collect_coverage_enabled,
            CIVisibility.test_skipping_enabled(),
        )

    @classmethod
    def disable(cls):
        # type: () -> None
        if cls._instance is None:
            log.debug("%s not enabled", cls.__name__)
            return
        log.debug("Disabling %s", cls.__name__)
        atexit.unregister(cls.disable)

        cls._instance.stop()
        cls._instance = None
        cls.enabled = False

        telemetry.telemetry_writer.periodic(force_flush=True)

        log.debug("%s disabled", cls.__name__)

    def _start_service(self):
        # type: () -> None
        tracer_filters = self.tracer._filters
        if not any(isinstance(tracer_filter, TraceCiVisibilityFilter) for tracer_filter in tracer_filters):
            tracer_filters += [TraceCiVisibilityFilter(self._tags, self._service)]  # type: ignore[arg-type]
            self.tracer.configure(settings={"FILTERS": tracer_filters})

        if self.test_skipping_enabled() and (not self._tests_to_skip and self._test_suites_to_skip is None):
            skipping_level = SUITE if self._suite_skipping_mode else TEST
            self._fetch_tests_to_skip(skipping_level)
            if self._suite_skipping_mode:
                if self._test_suites_to_skip is None:
                    skippable_items_count = 0
                    log.warning("Suites to skip remains None after fetching tests")
                else:
                    skippable_items_count = len(self._test_suites_to_skip)
            else:
                skippable_items_count = sum([len(skippable_tests) for skippable_tests in self._tests_to_skip.values()])
            log.info("Intelligent Test Runner skipping level: %s", skipping_level)
            log.info("Skippable items fetched: %s", skippable_items_count)

    def _stop_service(self):
        # type: () -> None
        if self._should_upload_git_metadata and not self._git_client.metadata_upload_finished():
            log.debug("git metadata upload still in progress, waiting before shutting down")
            try:
                try:
                    self._git_client._wait_for_metadata_upload(timeout=self.tracer.SHUTDOWN_TIMEOUT)
                except ValueError:
                    log.debug("Error waiting for metadata upload to complete during shutdown", exc_info=True)
            except TimeoutError:
                log.debug("Timed out waiting for metadata upload to complete during shutdown.")
        try:
            self.tracer.shutdown()
        except Exception:
            log.warning("Failed to shutdown tracer", exc_info=True)

    @classmethod
    def set_codeowners_of(cls, location, span=None):
        if not cls.enabled or cls._instance is None or cls._instance._codeowners is None or not location:
            return

        span = span or cls._instance.tracer.current_span()
        if span is None:
            return

        try:
            handles = cls._instance._codeowners.of(location)
            if handles:
                span.set_tag(test.CODEOWNERS, json.dumps(handles))
            else:
                log.debug("no matching codeowners for %s", location)
        except:  # noqa: E722
            log.debug("Error setting codeowners for %s", location, exc_info=True)

    @classmethod
    def add_session(cls, session: CIVisibilitySession):
        log.debug("Adding session: %s", session)
        if cls._instance is None:
            error_msg = "CI Visibility is not enabled"
            log.warning(error_msg)
            raise CIVisibilityError(error_msg)
        if cls._instance._session is not None:
            log.warning("Session already exists: %s", cls._instance._session)
            return
        cls._instance._session = session

    @classmethod
    def get_item_by_id(
        cls,
        item_id: CIItemId,
    ):
        if cls._instance is None:
            error_msg = "CI Visibility is not enabled"
            log.warning(error_msg)
            raise CIVisibilityError(error_msg)
        if isinstance(item_id, _CISessionId):
            return cls.get_session()
        if isinstance(item_id, CIModuleId):
            return cls.get_module_by_id(item_id)
        if isinstance(item_id, CISuiteId):
            return cls.get_suite_by_id(item_id)
        if isinstance(item_id, CITestId):
            return cls.get_test_by_id(item_id)
        error_msg = f"Unknown item id type: {type(item_id)}"
        log.warning(error_msg)
        raise CIVisibilityError(error_msg)

    @classmethod
    def get_session(cls) -> CIVisibilitySession:
        if cls._instance is None:
            error_msg = "CI Visibility is not enabled"
            log.warning(error_msg)
            raise CIVisibilityError(error_msg)
        if cls._instance._session is None:
            error_msg = "No session exists"
            log.warning(error_msg)
            raise CIVisibilityError(error_msg)
        return cls._instance._session

    @classmethod
    def get_module_by_id(cls, module_id: CIModuleId) -> CIVisibilityModule:
        if cls._instance is None:
            error_msg = "CI Visibility is not enabled"
            log.warning(error_msg)
            raise CIVisibilityError(error_msg)
        return cls.get_session().get_child_by_id(module_id)

    @classmethod
    def get_suite_by_id(cls, suite_id: CISuiteId) -> CIVisibilitySuite:
        if cls._instance is None:
            error_msg = "CI Visibility is not enabled"
            log.warning(error_msg)
            raise CIVisibilityError(error_msg)
        return cls.get_module_by_id(suite_id.parent_id).get_child_by_id(suite_id)

    @classmethod
    def get_test_by_id(cls, test_id: CITestId) -> CIVisibilityTest:
        if cls._instance is None:
            error_msg = "CI Visibility is not enabled"
            log.warning(error_msg)
            raise CIVisibilityError(error_msg)
        return cls.get_suite_by_id(test_id.parent_id).get_child_by_id(test_id)

    @classmethod
    def get_session_settings(cls) -> CIVisibilitySessionSettings:
        if cls._instance is None:
            error_msg = "CI Visibility is not enabled"
            log.warning(error_msg)
            raise CIVisibilityError(error_msg)
        return cls.get_session().get_session_settings()

    @classmethod
    def get_instance(cls) -> "CIVisibility":
        if not cls.enabled:
            error_msg = "CI Visibility is not enabled"
            log.warning(error_msg)
            raise CIVisibilityError(error_msg)
        if cls._instance is None:
            error_msg = "CI Visibility is enabled but _instance is None"
            log.warning(error_msg)
            raise CIVisibilityError(error_msg)
        return cls._instance

    @classmethod
    def get_tracer(cls) -> Optional[Tracer]:
        if not cls.enabled:
            error_msg = "CI Visibility is not enabled"
            log.warning(error_msg)
            raise CIVisibilityError(error_msg)
        instance = cls.get_instance()
        if instance is None:
            return None
        return instance.tracer

    @classmethod
    def get_service(cls) -> Optional[str]:
        if not cls.enabled:
            error_msg = "CI Visibility is not enabled"
            log.warning(error_msg)
            raise CIVisibilityError(error_msg)
        instance = cls.get_instance()
        if instance is None:
            return None
        return instance._service

    @classmethod
    def get_codeowners(cls) -> Optional[Codeowners]:
        if not cls.enabled:
            error_msg = "CI Visibility is not enabled"
            log.warning(error_msg)
            raise CIVisibilityError(error_msg)
        instance = cls.get_instance()
        if instance is None:
            return None
        return instance._codeowners

    @classmethod
    def get_workspace_path(cls) -> Optional[str]:
        if not cls.enabled:
            error_msg = "CI Visibility is not enabled"
            log.warning(error_msg)
            raise CIVisibilityError(error_msg)
        instance = cls.get_instance()
        if instance is None:
            return None
        return instance._tags.get(ci.WORKSPACE_PATH)

    @classmethod
    def is_item_itr_skippable(cls, item_id: CIItemId) -> bool:
        if not cls.enabled:
            error_msg = "CI Visibility is not enabled"
            log.warning(error_msg)
            raise CIVisibilityError(error_msg)
        instance = cls.get_instance()
        if instance is None:
            return False

        if instance._suite_skipping_mode:
            if isinstance(item_id, CISuiteId):
                return CIVisibility.is_suite_itr_skippable(item_id)
            log.debug("Skipping mode is suite, but item is not a suite: %s", item_id)
            return False

        if isinstance(item_id, CITestId):
            return CIVisibility.is_test_itr_skippable(item_id)
        log.debug("Skipping mode is test, but item is not a test: %s", item_id)

        return False

    @classmethod
    def is_suite_itr_skippable(cls, item_id: CISuiteId) -> bool:
        instance = cls.get_instance()
        if instance is None:
            return False
        item_module_path = item_id.parent_id.name.replace(".", "/")
        item_path = "/".join((item_module_path, item_id.name)) if item_module_path else item_id.name
        return instance._test_suites_to_skip is not None and item_path in instance._test_suites_to_skip

    @classmethod
    def is_test_itr_skippable(cls, item_id: CITestId) -> bool:
        instance = cls.get_instance()
        if instance is None:
            return False

        item_module_path = item_id.parent_id.parent_id.name.replace(".", "/")
        item_suite = item_id.parent_id.name
        item_path = "/".join((item_module_path, item_suite)) if item_module_path else item_suite

        return item_id.name in instance._tests_to_skip.get(item_path, [])

    @classmethod
    def is_unknown_ci(cls) -> bool:
        instance = cls.get_instance()
        if instance is None:
            return False

        return instance._tags.get(ci.PROVIDER_NAME) is None


def _requires_civisibility_enabled(func):
    def wrapper(*args, **kwargs):
        if not CIVisibility.enabled:
            log.warning("CI Visibility is not enabled")
            raise CIVisibilityError("CI Visibility is not enabled")
        return func(*args, **kwargs)

    return wrapper


@_requires_civisibility_enabled
def _on_discover_session(
    discover_args: CISession.DiscoverArgs, test_framework_telemetry_name: Optional[TEST_FRAMEWORKS] = None
):
    log.debug("Handling session discovery")

    # _requires_civisibility_enabled prevents us from getting here, but this makes type checkers happy
    tracer = CIVisibility.get_tracer()
    test_service = CIVisibility.get_service()
    instance = CIVisibility.get_instance()

    if tracer is None or test_service is None:
        error_msg = "Tracer or test service is None"
        log.warning(error_msg)
        raise CIVisibilityError(error_msg)

    # If we're not provided a root directory, try and extract it from workspace, defaulting to CWD
    workspace_path = discover_args.root_dir or Path(CIVisibility.get_workspace_path() or os.getcwd())

    test_framework_telemetry_name = test_framework_telemetry_name or TEST_FRAMEWORKS.MANUAL

    session_settings = CIVisibilitySessionSettings(
        tracer=tracer,
        test_service=test_service,
        test_command=discover_args.test_command,
        reject_duplicates=discover_args.reject_duplicates,
        test_framework=discover_args.test_framework,
        test_framework_metric_name=test_framework_telemetry_name,
        test_framework_version=discover_args.test_framework_version,
        session_operation_name=discover_args.session_operation_name,
        module_operation_name=discover_args.module_operation_name,
        suite_operation_name=discover_args.suite_operation_name,
        test_operation_name=discover_args.test_operation_name,
        workspace_path=workspace_path,
        is_unsupported_ci=CIVisibility.is_unknown_ci(),
        itr_enabled=CIVisibility.is_itr_enabled(),
        itr_test_skipping_enabled=CIVisibility.test_skipping_enabled(),
        itr_test_skipping_level=SUITE if instance._suite_skipping_mode else TEST,
        itr_correlation_id=instance._itr_meta.get(ITR_CORRELATION_ID_TAG_NAME, ""),
        coverage_enabled=CIVisibility.should_collect_coverage(),
    )

    session = CIVisibilitySession(
        session_settings,
    )

    CIVisibility.add_session(session)


@_requires_civisibility_enabled
def _on_start_session():
    log.debug("Handling start session")
    session = CIVisibility.get_session()
    session.start()


@_requires_civisibility_enabled
def _on_finish_session(finish_args: CISession.FinishArgs):
    log.debug("Handling finish session")
    session = CIVisibility.get_session()
    session.finish(finish_args.force_finish_children, finish_args.override_status)


@_requires_civisibility_enabled
def _on_session_is_test_skipping_enabled() -> Path:
    log.debug("Handling is test skipping enabled")
    return CIVisibility.test_skipping_enabled()


@_requires_civisibility_enabled
def _on_session_get_workspace_path() -> Path:
    log.debug("Handling finish for session id %s")
    session_settings = CIVisibility.get_session().get_session_settings()
    return session_settings.workspace_path


@_requires_civisibility_enabled
def _on_session_should_collect_coverage() -> bool:
    """Code coverage collection is not tied to any given session ID"""
    log.debug("Handling should collect coverage")
    return CIVisibility.should_collect_coverage()


@_requires_civisibility_enabled
def _on_session_get_codeowners() -> Optional[Codeowners]:
    """The codeowners object is not related to any given session ID"""
    log.debug("Getting codeowners")
    return CIVisibility.get_codeowners()


def _register_session_handlers():
    log.debug("Registering session handlers")
    core.on("ci_visibility.session.discover", _on_discover_session)
    core.on("ci_visibility.session.start", _on_start_session)
    core.on("ci_visibility.session.finish", _on_finish_session)
    core.on("ci_visibility.session.get_codeowners", _on_session_get_codeowners, "codeowners")
    core.on("ci_visibility.session.get_workspace_path", _on_session_get_workspace_path, "workspace_path")
    core.on(
        "ci_visibility.session.should_collect_coverage", _on_session_should_collect_coverage, "should_collect_coverage"
    )
    core.on(
        "ci_visibility.session.is_test_skipping_enabled",
        _on_session_is_test_skipping_enabled,
        "is_test_skipping_enabled",
    )


@_requires_civisibility_enabled
def _on_discover_module(discover_args: CIModule.DiscoverArgs):
    log.debug("Handling discovery for module %s", discover_args.module_id)
    session = CIVisibility.get_session()

    session.add_child(
        discover_args.module_id,
        CIVisibilityModule(
            discover_args.module_id.name,
            discover_args.module_path,
            CIVisibility.get_session_settings(),
        ),
    )


@_requires_civisibility_enabled
def _on_start_module(module_id: CIModuleId):
    log.debug("Handling start for module id %s", module_id)
    CIVisibility.get_module_by_id(module_id).start()


@_requires_civisibility_enabled
def _on_finish_module(finish_args: CIModule.FinishArgs):
    log.debug("Handling finish for module id %s", finish_args.module_id)
    CIVisibility.get_module_by_id(finish_args.module_id).finish()


def _register_module_handlers():
    log.debug("Registering module handlers")
    core.on("ci_visibility.module.discover", _on_discover_module)
    core.on("ci_visibility.module.start", _on_start_module)
    core.on("ci_visibility.module.finish", _on_finish_module)


@_requires_civisibility_enabled
def _on_discover_suite(discover_args: CISuite.DiscoverArgs):
    log.debug("Handling discovery for suite args %s", discover_args)
    module = CIVisibility.get_module_by_id(discover_args.suite_id.parent_id)

    module.add_child(
        discover_args.suite_id,
        CIVisibilitySuite(
            discover_args.suite_id.name,
            CIVisibility.get_session_settings(),
            discover_args.codeowners,
            discover_args.source_file_info,
        ),
    )


@_requires_civisibility_enabled
def _on_start_suite(suite_id: CISuiteId):
    log.debug("Handling start for suite id %s", suite_id)
    CIVisibility.get_suite_by_id(suite_id).start()


@_requires_civisibility_enabled
def _on_finish_suite(finish_args: CISuite.FinishArgs):
    log.debug("Handling finish for suite id %s", finish_args.suite_id)
    CIVisibility.get_suite_by_id(finish_args.suite_id).finish(
        finish_args.force_finish_children, finish_args.override_status
    )


def _register_suite_handlers():
    log.debug("Registering suite handlers")
    core.on("ci_visibility.suite.discover", _on_discover_suite)
    core.on("ci_visibility.suite.start", _on_start_suite)
    core.on("ci_visibility.suite.finish", _on_finish_suite)


@_requires_civisibility_enabled
def _on_discover_test(discover_args: CITest.DiscoverArgs):
    log.debug("Handling discovery for test %s", discover_args.test_id)
    suite = CIVisibility.get_suite_by_id(discover_args.test_id.parent_id)

    suite.add_child(
        discover_args.test_id,
        CIVisibilityTest(
            discover_args.test_id.name,
            CIVisibility.get_session_settings(),
            parameters=discover_args.test_id.parameters,
            codeowners=discover_args.codeowners,
            source_file_info=discover_args.source_file_info,
            resource=discover_args.resource,
        ),
    )


@_requires_civisibility_enabled
def _on_discover_test_early_flake_retry(args: CITest.DiscoverEarlyFlakeRetryArgs):
    log.debug("Handling early flake discovery for test %s", args.test_id)
    try:
        original_test = CIVisibility.get_test_by_id(args.test_id)
    except CIVisibilityDataError:
        log.warning("Cannot find original test %s to register retry number %s", args.test_id, args.retry_number)
        raise

    original_test.make_early_flake_retry_from_test(args.test_id, args.retry_number)


@_requires_civisibility_enabled
def _on_start_test(test_id: CITestId):
    log.debug("Handling start for test id %s", test_id)
    CIVisibility.get_test_by_id(test_id).start()


@_requires_civisibility_enabled
def _on_finish_test(finish_args: CITest.FinishArgs):
    log.debug("Handling finish for test id %s, with status %s", finish_args.test_id, finish_args.status)
    CIVisibility.get_test_by_id(finish_args.test_id).finish_test(
        finish_args.status, finish_args.skip_reason, finish_args.exc_info
    )


def _register_test_handlers():
    log.debug("Registering test handlers")
    core.on("ci_visibility.test.discover", _on_discover_test)
    core.on("ci_visibility.test.discover_early_flake_retry", _on_discover_test_early_flake_retry)
    core.on("ci_visibility.test.start", _on_start_test)
    core.on("ci_visibility.test.finish", _on_finish_test)


@_requires_civisibility_enabled
def _on_item_get_span(item_id: CIItemId):
    log.debug("Handing get_span for item %s", item_id)
    item = CIVisibility.get_item_by_id(item_id)
    return item.get_span()


@_requires_civisibility_enabled
def _on_item_is_finished(item_id: CIItemId) -> bool:
    log.debug("Handling is finished for item %s", item_id)
    return CIVisibility.get_item_by_id(item_id).is_finished()


def _register_item_handlers():
    log.debug("Registering item handlers")
    core.on("ci_visibility.item.get_span", _on_item_get_span, "span")
    core.on("ci_visibility.item.is_finished", _on_item_is_finished, "is_finished")


@_requires_civisibility_enabled
def _on_add_coverage_data(add_coverage_args: CIITRMixin.AddCoverageArgs):
    """Adds coverage data to an item, merging with existing coverage data if necessary"""
    item_id = add_coverage_args.item_id
    coverage_data = add_coverage_args.coverage_data

    log.debug("Handling add coverage data for item id %s", item_id)

    if not isinstance(item_id, (CISuiteId, CITestId)):
        log.warning("Coverage data can only be added to suites and tests, not %s", type(item_id))
        return

    CIVisibility.get_item_by_id(item_id).add_coverage_data(coverage_data)


def _register_coverage_handlers():
    log.debug("Registering coverage handlers")
    core.on("ci_visibility.item.add_coverage_data", _on_add_coverage_data)


@_requires_civisibility_enabled
def _on_get_tag(get_tag_args: CIBase.GetTagArgs) -> Any:
    item_id = get_tag_args.item_id
    key = get_tag_args.name
    log.debug("Handling get tag for item id %s, key %s", item_id, key)
    return CIVisibility.get_item_by_id(item_id).get_tag(key)


@_requires_civisibility_enabled
def _on_set_tag(set_tag_args: CIBase.SetTagArgs) -> None:
    item_id = set_tag_args.item_id
    key = set_tag_args.name
    value = set_tag_args.value
    log.debug("Handling set tag for item id %s, key %s, value %s", item_id, key, value)
    CIVisibility.get_item_by_id(item_id).set_tag(key, value)


@_requires_civisibility_enabled
def _on_set_tags(set_tags_args: CIBase.SetTagsArgs) -> None:
    item_id = set_tags_args.item_id
    tags = set_tags_args.tags
    log.debug("Handling set tags for item id %s, tags %s", item_id, tags)
    CIVisibility.get_item_by_id(item_id).set_tags(tags)


@_requires_civisibility_enabled
def _on_delete_tag(delete_tag_args: CIBase.DeleteTagArgs) -> None:
    item_id = delete_tag_args.item_id
    key = delete_tag_args.name
    log.debug("Handling delete tag for item id %s, key %s", item_id, key)
    CIVisibility.get_item_by_id(item_id).delete_tag(key)


@_requires_civisibility_enabled
def _on_delete_tags(delete_tags_args: CIBase.DeleteTagsArgs) -> None:
    item_id = delete_tags_args.item_id
    keys = delete_tags_args.names
    log.debug("Handling delete tags for item id %s, keys %s", item_id, keys)
    CIVisibility.get_item_by_id(item_id).delete_tags(keys)


def _register_tag_handlers():
    log.debug("Registering tag handlers")
    core.on("ci_visibility.item.get_tag", _on_get_tag, "tag_value")
    core.on("ci_visibility.item.set_tag", _on_set_tag)
    core.on("ci_visibility.item.set_tags", _on_set_tags)
    core.on("ci_visibility.item.delete_tag", _on_delete_tag)
    core.on("ci_visibility.item.delete_tags", _on_delete_tags)


@_requires_civisibility_enabled
def _on_itr_finish_item_skipped(item_id: Union[CISuiteId, CITestId]) -> None:
    log.debug("Handling finish ITR skipped for item id %s", item_id)
    if not isinstance(item_id, (CISuiteId, CITestId)):
        log.warning("Only suites or tests can be skipped, not %s", type(item_id))
        return
    CIVisibility.get_item_by_id(item_id).finish_itr_skipped()


@_requires_civisibility_enabled
def _on_itr_mark_unskippable(item_id: Union[CISuiteId, CITestId]) -> None:
    log.debug("Handling marking %s unskippable", item_id)
    CIVisibility.get_item_by_id(item_id).mark_itr_unskippable()


@_requires_civisibility_enabled
def _on_itr_mark_forced_run(item_id: Union[CISuiteId, CITestId]) -> None:
    log.debug("Handling marking %s as forced run", item_id)
    CIVisibility.get_item_by_id(item_id).mark_itr_forced_run()


@_requires_civisibility_enabled
def _on_itr_was_forced_run(item_id: CIItemId) -> bool:
    log.debug("Handling marking %s as forced run", item_id)
    return CIVisibility.get_item_by_id(item_id).was_itr_forced_run()


@_requires_civisibility_enabled
def _on_itr_is_item_skippable(item_id: Union[CISuiteId, CITestId]) -> bool:
    """Skippable items are fetched as part CIVisibility.enable(), so they are assumed to be available."""
    log.debug("Handling is item skippable for item id %s", item_id)

    if not isinstance(item_id, (CISuiteId, CITestId)):
        log.warning("Only suites or tests can be skippable, not %s", type(item_id))
        return False

    if not CIVisibility.test_skipping_enabled():
        log.debug("Test skipping is not enabled")
        return False

    return CIVisibility.is_item_itr_skippable(item_id)


@_requires_civisibility_enabled
def _on_itr_is_item_unskippable(item_id: Union[CISuiteId, CITestId]) -> bool:
    log.debug("Handling is item unskippable for %s", item_id)
    if not isinstance(item_id, (CISuiteId, CITestId)):
        raise CIVisibilityError("Only suites or tests can be unskippable")
    return CIVisibility.get_item_by_id(item_id).is_itr_unskippable()


@_requires_civisibility_enabled
def _on_itr_was_item_skipped(item_id: Union[CISuiteId, CITestId]) -> bool:
    log.debug("Handling was item skipped for %s", item_id)
    return CIVisibility.get_item_by_id(item_id).is_itr_skipped()


def _register_itr_handlers():
    log.debug("Registering ITR-related handlers")
    core.on("ci_visibility.itr.finish_skipped_by_itr", _on_itr_finish_item_skipped)
    core.on("ci_visibility.itr.is_item_skippable", _on_itr_is_item_skippable, "is_item_skippable")
    core.on("ci_visibility.itr.was_item_skipped", _on_itr_was_item_skipped, "was_item_skipped")

    core.on("ci_visibility.itr.is_item_unskippable", _on_itr_is_item_unskippable, "is_item_unskippable")
    core.on("ci_visibility.itr.mark_forced_run", _on_itr_mark_forced_run)
    core.on("ci_visibility.itr.mark_unskippable", _on_itr_mark_unskippable)
    core.on("ci_visibility.itr.was_forced_run", _on_itr_was_forced_run, "was_forced_run")


_register_session_handlers()
_register_module_handlers()
_register_suite_handlers()
_register_test_handlers()
_register_item_handlers()
_register_tag_handlers()
_register_coverage_handlers()
_register_itr_handlers()
