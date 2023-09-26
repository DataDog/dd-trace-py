from collections import defaultdict
import json
import os
from typing import TYPE_CHECKING
from uuid import uuid4

from ddtrace import Tracer
from ddtrace import config as ddconfig
from ddtrace.contrib import trace_utils
from ddtrace.ext import ci
from ddtrace.ext import test
from ddtrace.internal import atexit
from ddtrace.internal import compat
from ddtrace.internal.agent import get_connection
from ddtrace.internal.agent import get_trace_url
from ddtrace.internal.ci_visibility.coverage import is_coverage_available
from ddtrace.internal.ci_visibility.filters import TraceCiVisibilityFilter
from ddtrace.internal.compat import JSONDecodeError
from ddtrace.internal.compat import TimeoutError
from ddtrace.internal.compat import parse
from ddtrace.internal.logger import get_logger
from ddtrace.internal.service import Service
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.writer.writer import Response
from ddtrace.provider import CIContextProvider

from .. import agent
from .constants import AGENTLESS_API_KEY_HEADER_NAME
from .constants import AGENTLESS_APP_KEY_HEADER_NAME
from .constants import AGENTLESS_DEFAULT_SITE
from .constants import EVP_NEEDS_APP_KEY_HEADER_NAME
from .constants import EVP_NEEDS_APP_KEY_HEADER_VALUE
from .constants import EVP_PROXY_AGENT_BASE_PATH
from .constants import EVP_SUBDOMAIN_HEADER_API_VALUE
from .constants import EVP_SUBDOMAIN_HEADER_EVENT_VALUE
from .constants import EVP_SUBDOMAIN_HEADER_NAME
from .constants import REQUESTS_MODE
from .constants import SETTING_ENDPOINT
from .constants import SKIPPABLE_ENDPOINT
from .constants import SUITE
from .constants import TEST
from .git_client import CIVisibilityGitClient
from .writer import CIVisibilityWriter


if TYPE_CHECKING:  # pragma: no cover
    from typing import Any
    from typing import DefaultDict
    from typing import Dict
    from typing import List
    from typing import Optional
    from typing import Tuple

    from ddtrace.settings import IntegrationConfig

log = get_logger(__name__)

DEFAULT_TIMEOUT = 15


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


def _do_request(method, url, payload, headers):
    # type: (str, str, str, Dict) -> Response
    try:
        conn = get_connection(url, timeout=DEFAULT_TIMEOUT)
        log.debug("Sending request: %s %s %s %s", method, url, payload, headers)
        conn.request("POST", url, payload, headers)
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
            # Create a new CI tracer
            self.tracer = Tracer(context_provider=CIContextProvider())

        self._app_key = os.getenv(
            "_CI_DD_APP_KEY",
            os.getenv("DD_APP_KEY", os.getenv("DD_APPLICATION_KEY", os.getenv("DATADOG_APPLICATION_KEY"))),
        )
        self._api_key = os.getenv("_CI_DD_API_KEY", os.getenv("DD_API_KEY"))

        self._dd_site = os.getenv("DD_SITE", AGENTLESS_DEFAULT_SITE)
        self._suite_skipping_mode = asbool(os.getenv("_DD_CIVISIBILITY_ITR_SUITE_MODE", default=False))
        self.config = config  # type: Optional[IntegrationConfig]
        self._tags = ci.tags(cwd=_get_git_repo())  # type: Dict[str, str]
        self._service = service
        self._codeowners = None
        self._root_dir = None

        int_service = None
        if self.config is not None:
            int_service = trace_utils.int_service(None, self.config)
        # check if repository URL detected from environment or .git, and service name unchanged
        if self._tags.get(ci.git.REPOSITORY_URL, None) and self.config and int_service == self.config._default_service:
            self._service = _extract_repository_name_from_url(self._tags[ci.git.REPOSITORY_URL])
        elif self._service is None and int_service is not None:
            self._service = int_service

        self._requests_mode = REQUESTS_MODE.TRACES
        if ddconfig._ci_visibility_agentless_enabled:
            if not self._api_key:
                raise EnvironmentError(
                    "DD_CIVISIBILITY_AGENTLESS_ENABLED is set, but DD_API_KEY is not set, so ddtrace "
                    "cannot be initialized."
                )
            self._requests_mode = REQUESTS_MODE.AGENTLESS_EVENTS
        elif self._agent_evp_proxy_is_available():
            self._requests_mode = REQUESTS_MODE.EVP_PROXY_EVENTS

        self._code_coverage_enabled_by_api, self._test_skipping_enabled_by_api = self._check_enabled_features()

        self._collect_coverage_enabled = self._should_collect_coverage(self._code_coverage_enabled_by_api)

        self._configure_writer(coverage_enabled=self._collect_coverage_enabled)
        self._git_client = None

        if ddconfig._ci_visibility_intelligent_testrunner_enabled:
            if self._app_key is None:
                log.warning("Environment variable DD_APP_KEY not set, so no git metadata will be uploaded.")
            elif self._requests_mode == REQUESTS_MODE.TRACES:
                log.warning("Cannot start git client if mode is not agentless or evp proxy")
            else:
                if not self._test_skipping_enabled_by_api:
                    log.warning("Intelligent Test Runner test skipping disabled by API")
                self._git_client = CIVisibilityGitClient(
                    api_key=self._api_key or "", app_key=self._app_key, requests_mode=self._requests_mode
                )
        try:
            from ddtrace.internal.codeowners import Codeowners

            self._codeowners = Codeowners()
        except ValueError:
            log.warning("CODEOWNERS file is not available")
        except Exception:
            log.warning("Failed to load CODEOWNERS", exc_info=True)

    @staticmethod
    def _should_collect_coverage(coverage_enabled_by_api):
        if not coverage_enabled_by_api:
            return False
        if compat.PY2:
            log.warning("CI Visibility code coverage tracking is enabled, but Python 2 is not supported.")
            return False
        if not is_coverage_available():
            log.warning(
                "CI Visibility code coverage tracking is enabled, but either the `coverage` package is not installed."
                "To use code coverage tracking, please install `coverage` from https://pypi.org/project/coverage/"
            )
            return False
        return True

    def _check_enabled_features(self):
        # type: () -> Tuple[bool, bool]
        # DEV: Remove this ``if`` once ITR is in GA
        if not ddconfig._ci_visibility_intelligent_testrunner_enabled:
            return False, False

        if self._requests_mode == REQUESTS_MODE.EVP_PROXY_EVENTS:
            url = get_trace_url() + EVP_PROXY_AGENT_BASE_PATH + SETTING_ENDPOINT
            _headers = {
                EVP_SUBDOMAIN_HEADER_NAME: EVP_SUBDOMAIN_HEADER_API_VALUE,
                EVP_NEEDS_APP_KEY_HEADER_NAME: EVP_NEEDS_APP_KEY_HEADER_VALUE,
            }
            log.debug("Making EVP request to agent: url=%s, headers=%s", url, _headers)
        elif self._requests_mode == REQUESTS_MODE.AGENTLESS_EVENTS:
            if not self._app_key or not self._api_key:
                log.debug("Cannot make request to setting endpoint if application key is not set")
                return False, False
            url = "https://api." + self._dd_site + SETTING_ENDPOINT
            _headers = {
                AGENTLESS_API_KEY_HEADER_NAME: self._api_key,
                AGENTLESS_APP_KEY_HEADER_NAME: self._app_key,
                "Content-Type": "application/json",
            }
        else:
            log.warning("Cannot make requests to setting endpoint if mode is not agentless or evp proxy")
            return False, False

        payload = {
            "data": {
                "id": str(uuid4()),
                "type": "ci_app_test_service_libraries_settings",
                "attributes": {
                    "service": self._service,
                    "env": ddconfig.env,
                    "repository_url": self._tags.get(ci.git.REPOSITORY_URL),
                    "sha": self._tags.get(ci.git.COMMIT_SHA),
                    "branch": self._tags.get(ci.git.BRANCH),
                },
            }
        }
        try:
            response = _do_request("POST", url, json.dumps(payload), _headers)
        except TimeoutError:
            log.warning("Request timeout while fetching enabled features")
            return False, False
        try:
            if isinstance(response.body, bytes):
                parsed = json.loads(response.body.decode())
            else:
                parsed = json.loads(response.body)
        except JSONDecodeError:
            log.warning("Settings request responded with invalid JSON '%s'", response.body)
            return False, False
        if response.status >= 400 or ("errors" in parsed and parsed["errors"][0] == "Not found"):
            log.warning(
                "Feature enablement check returned status %d - disabling Intelligent Test Runner", response.status
            )
            return False, False

        attributes = parsed["data"]["attributes"]
        return attributes["code_coverage"], attributes["tests_skipping"]

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
        if not cls.enabled:
            return False
        return cls._instance and cls._instance._test_skipping_enabled_by_api

    def _fetch_tests_to_skip(self, skipping_mode):
        # Make sure git uploading has finished
        # this will block the thread until that happens
        if self._git_client is not None:
            self._git_client.shutdown()
            self._git_client = None

        payload = {
            "data": {
                "type": "test_params",
                "attributes": {
                    "service": self._service,
                    "env": ddconfig.env,
                    "repository_url": self._tags.get(ci.git.REPOSITORY_URL),
                    "sha": self._tags.get(ci.git.COMMIT_SHA),
                    "configurations": ci._get_runtime_and_os_metadata(),
                    "test_level": skipping_mode,
                },
            }
        }

        _headers = {
            "dd-api-key": self._api_key,
            "dd-application-key": self._app_key,
            "Content-Type": "application/json",
        }
        if self._requests_mode == REQUESTS_MODE.EVP_PROXY_EVENTS:
            url = get_trace_url() + EVP_PROXY_AGENT_BASE_PATH + SKIPPABLE_ENDPOINT
            _headers = {
                EVP_SUBDOMAIN_HEADER_NAME: EVP_SUBDOMAIN_HEADER_API_VALUE,
                EVP_NEEDS_APP_KEY_HEADER_NAME: EVP_NEEDS_APP_KEY_HEADER_VALUE,
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
            log.warning("Test skips request responded with status %d", response.status)
            return
        try:
            if isinstance(response.body, bytes):
                parsed = json.loads(response.body.decode())
            else:
                parsed = json.loads(response.body)
        except json.JSONDecodeError:
            log.warning("Test skips request responded with invalid JSON '%s'", response.body)
            return

        for item in parsed["data"]:
            if item["type"] == skipping_mode and "suite" in item["attributes"]:
                module = item["attributes"].get("configurations", {}).get("test.bundle", "").replace(".", "/")
                path = "/".join((module, item["attributes"]["suite"])) if module else item["attributes"]["suite"]

                if skipping_mode == SUITE:
                    self._test_suites_to_skip.append(path)
                else:
                    self._tests_to_skip[path].append(item["attributes"]["name"])

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

        log.debug("%s disabled", cls.__name__)

    def _start_service(self):
        # type: () -> None
        tracer_filters = self.tracer._filters
        if not any(isinstance(tracer_filter, TraceCiVisibilityFilter) for tracer_filter in tracer_filters):
            tracer_filters += [TraceCiVisibilityFilter(self._tags, self._service)]  # type: ignore[arg-type]
            self.tracer.configure(settings={"FILTERS": tracer_filters})
        if self._git_client is not None:
            self._git_client.start(cwd=_get_git_repo())
        if self.test_skipping_enabled() and (not self._tests_to_skip and self._test_suites_to_skip is None):
            self._fetch_tests_to_skip(SUITE if self._suite_skipping_mode else TEST)

    def _stop_service(self):
        # type: () -> None
        if self._git_client is not None:
            self._git_client.shutdown(timeout=self.tracer.SHUTDOWN_TIMEOUT)
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
