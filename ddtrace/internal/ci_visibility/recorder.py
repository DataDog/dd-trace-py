from collections import defaultdict
import json
import os
from typing import TYPE_CHECKING  # noqa:F401
from typing import NamedTuple  # noqa:F401
from uuid import uuid4

from ddtrace import Tracer
from ddtrace import config as ddconfig
from ddtrace.contrib import trace_utils
from ddtrace.ext import ci
from ddtrace.ext import test
from ddtrace.internal import atexit
from ddtrace.internal import compat
from ddtrace.internal import telemetry
from ddtrace.internal.agent import get_connection
from ddtrace.internal.agent import get_trace_url
from ddtrace.internal.ci_visibility.coverage import is_coverage_available
from ddtrace.internal.ci_visibility.filters import TraceCiVisibilityFilter
from ddtrace.internal.compat import JSONDecodeError
from ddtrace.internal.compat import parse
from ddtrace.internal.logger import get_logger
from ddtrace.internal.service import Service
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.writer.writer import Response
from ddtrace.provider import CIContextProvider

from .. import agent
from ..utils.http import verify_url
from ..utils.time import StopWatch
from .constants import AGENTLESS_API_KEY_HEADER_NAME
from .constants import AGENTLESS_DEFAULT_SITE
from .constants import CUSTOM_CONFIGURATIONS_PREFIX
from .constants import EVP_PROXY_AGENT_BASE_PATH
from .constants import EVP_SUBDOMAIN_HEADER_API_VALUE
from .constants import EVP_SUBDOMAIN_HEADER_EVENT_VALUE
from .constants import EVP_SUBDOMAIN_HEADER_NAME
from .constants import REQUESTS_MODE
from .constants import SETTING_ENDPOINT
from .constants import SKIPPABLE_ENDPOINT
from .constants import SUITE
from .constants import TEST
from .git_client import METADATA_UPLOAD_STATUS
from .git_client import CIVisibilityGitClient
from .telemetry.constants import ERROR_TYPES
from .telemetry.git import record_settings
from .writer import CIVisibilityWriter


if TYPE_CHECKING:  # pragma: no cover
    from typing import Any  # noqa:F401
    from typing import DefaultDict  # noqa:F401
    from typing import Dict  # noqa:F401
    from typing import List  # noqa:F401
    from typing import Optional  # noqa:F401
    from typing import Tuple  # noqa:F401

    from ddtrace.settings import IntegrationConfig  # noqa:F401

log = get_logger(__name__)

DEFAULT_TIMEOUT = 15

_CIVisiblitySettings = NamedTuple(
    "_CIVisiblitySettings",
    [("coverage_enabled", bool), ("skipping_enabled", bool), ("require_git", bool), ("itr_enabled", bool)],
)


def _extract_repository_name_from_url(repository_url):
    # type: (str) -> str
    try:
        return parse.urlparse(repository_url).path.rstrip(".git").rpartition("/")[-1]
    except ValueError:
        # In case of parsing error, default to repository url
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


def _do_request(method, url, payload, headers):
    # type: (str, str, str, Dict) -> Response
    try:
        parsed_url = verify_url(url)
        url_path = parsed_url.path
        conn = get_connection(url, timeout=DEFAULT_TIMEOUT)
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

        telemetry.telemetry_writer.enable()

        if tracer:
            self.tracer = tracer
        else:
            if asbool(os.getenv("_DD_CIVISIBILITY_USE_CI_CONTEXT_PROVIDER")):
                # Create a new CI tracer
                self.tracer = Tracer(context_provider=CIContextProvider())
            else:
                self.tracer = Tracer()

        self._configurations = ci._get_runtime_and_os_metadata()
        custom_configurations = _get_custom_configurations()
        if custom_configurations:
            self._configurations["custom"] = custom_configurations

        self._api_key = os.getenv("_CI_DD_API_KEY", os.getenv("DD_API_KEY"))

        self._dd_site = os.getenv("DD_SITE", AGENTLESS_DEFAULT_SITE)
        self._suite_skipping_mode = asbool(os.getenv("_DD_CIVISIBILITY_ITR_SUITE_MODE", default=False))
        self.config = config  # type: Optional[IntegrationConfig]
        self._tags = ci.tags(cwd=_get_git_repo())  # type: Dict[str, str]
        self._service = service
        self._codeowners = None
        self._root_dir = None
        self._should_upload_git_metadata = True

        int_service = None
        if self.config is not None:
            int_service = trace_utils.int_service(None, self.config)
        # check if repository URL detected from environment or .git, and service name unchanged
        if self._tags.get(ci.git.REPOSITORY_URL, None) and self.config and int_service == self.config._default_service:
            self._service = _extract_repository_name_from_url(self._tags[ci.git.REPOSITORY_URL])
        elif self._service is None and int_service is not None:
            self._service = int_service

        if ddconfig._ci_visibility_agentless_enabled:
            if not self._api_key:
                raise EnvironmentError(
                    "DD_CIVISIBILITY_AGENTLESS_ENABLED is set, but DD_API_KEY is not set, so ddtrace "
                    "cannot be initialized."
                )
            log.info("Datadog CI Visibility using agentless mode")
            self._requests_mode = REQUESTS_MODE.AGENTLESS_EVENTS
        elif self._agent_evp_proxy_is_available():
            log.info("Datadog CI Visibility using EVP proxy mode")
            self._requests_mode = REQUESTS_MODE.EVP_PROXY_EVENTS
        else:
            log.info("Datadog CI Visibility using APM mode, some features will be disabled")
            self._requests_mode = REQUESTS_MODE.TRACES
            self._should_upload_git_metadata = False

        if self._should_upload_git_metadata:
            self._git_client = CIVisibilityGitClient(api_key=self._api_key or "", requests_mode=self._requests_mode)
            self._git_client.upload_git_metadata(cwd=_get_git_repo())

        self._code_coverage_enabled_by_api, self._test_skipping_enabled_by_api = self._check_enabled_features()

        self._collect_coverage_enabled = self._should_collect_coverage(self._code_coverage_enabled_by_api)

        self._configure_writer(coverage_enabled=self._collect_coverage_enabled)

        if not self._test_skipping_enabled_by_api:
            log.warning("Intelligent Test Runner test skipping disabled by API")

        try:
            from ddtrace.internal.codeowners import Codeowners

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
                "CI Visibility code coverage tracking is enabled, but either the `coverage` package is not installed."
                "To use code coverage tracking, please install `coverage` from https://pypi.org/project/coverage/"
            )
            return False
        return True

    def _check_settings_api(self, url, headers):
        # type: (str, Dict[str, str]) -> _CIVisiblitySettings
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

        return _CIVisiblitySettings(coverage_enabled, skipping_enabled, require_git, itr_enabled)

    def _check_enabled_features(self):
        # type: () -> Tuple[bool, bool]
        # DEV: Remove this ``if`` once ITR is in GA
        if not ddconfig._ci_visibility_intelligent_testrunner_enabled:
            return False, False

        if self._requests_mode == REQUESTS_MODE.EVP_PROXY_EVENTS:
            url = get_trace_url() + EVP_PROXY_AGENT_BASE_PATH + SETTING_ENDPOINT
            _headers = {
                EVP_SUBDOMAIN_HEADER_NAME: EVP_SUBDOMAIN_HEADER_API_VALUE,
            }
            log.debug("Making EVP request to agent: url=%s, headers=%s", url, _headers)
        elif self._requests_mode == REQUESTS_MODE.AGENTLESS_EVENTS:
            if not self._api_key:
                log.debug("Cannot make request to setting endpoint if API key is not set")
                return False, False
            url = "https://api." + self._dd_site + SETTING_ENDPOINT
            _headers = {
                AGENTLESS_API_KEY_HEADER_NAME: self._api_key,
                "Content-Type": "application/json",
            }
        else:
            log.warning("Cannot make requests to setting endpoint if mode is not agentless or evp proxy")
            return False, False

        try:
            settings = self._check_settings_api(url, _headers)
        except Exception:
            log.warning(
                "Error checking Intelligent Test Runner API, disabling coverage collection and test skipping",
                exc_info=True,
            )
            return False, False

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
                return False, False
            if settings.require_git:
                log.warning("git metadata upload did not complete in time, test skipping will be best effort")

        return settings.coverage_enabled, settings.skipping_enabled

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
    def test_skipping_enabled(cls):
        if not cls.enabled or asbool(os.getenv("_DD_CIVISIBILITY_ITR_PREVENT_TEST_SKIPPING", default=False)):
            return False
        return cls._instance and cls._instance._test_skipping_enabled_by_api

    def _fetch_tests_to_skip(self, skipping_mode):
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
        else:
            log.warning("Cannot make requests to skippable endpoint if mode is not agentless or evp proxy")
            return

        try:
            response = _do_request("POST", url, json.dumps(payload), _headers)
        except TimeoutError:
            log.warning("Request timeout while fetching skippable tests")
            self._test_suites_to_skip = []
            return

        self._test_suites_to_skip = []

        if response.status >= 400:
            log.warning("Skippable tests request responded with status %d", response.status)
            return
        try:
            if isinstance(response.body, bytes):
                parsed = json.loads(response.body.decode())
            else:
                parsed = json.loads(response.body)
        except json.JSONDecodeError:
            log.warning("Skippable tests request responded with invalid JSON '%s'", response.body)
            return

        if "data" not in parsed:
            log.warning("Skippable tests request missing data, no tests will be skipped")
            return

        try:
            for item in parsed["data"]:
                if item["type"] == skipping_mode and "suite" in item["attributes"]:
                    module = item["attributes"].get("configurations", {}).get("test.bundle", "").replace(".", "/")
                    path = "/".join((module, item["attributes"]["suite"])) if module else item["attributes"]["suite"]

                    if skipping_mode == SUITE:
                        self._test_suites_to_skip.append(path)
                    else:
                        self._tests_to_skip[path].append(item["attributes"]["name"])
        except Exception:
            log.warning("Error processing skippable test data, no tests will be skipped", exc_info=True)
            self._test_suites_to_skip = []
            self._tests_to_skip = defaultdict(list)

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

        cls._instance = cls(tracer=tracer, config=config, service=service)
        cls.enabled = True

        cls._instance.start()
        atexit.register(cls.disable)

        log.debug("%s enabled", cls.__name__)

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
            self._fetch_tests_to_skip(SUITE if self._suite_skipping_mode else TEST)

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
        except KeyError:
            log.debug("no matching codeowners for %s", location)
