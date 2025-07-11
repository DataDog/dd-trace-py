from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
from typing import Any
from typing import Callable
from typing import Dict
from typing import Optional
from typing import Set
from typing import Union
from urllib import parse

import ddtrace
from ddtrace import config as ddconfig
from ddtrace.contrib import trace_utils
from ddtrace.ext import ci
from ddtrace.ext import test
from ddtrace.ext.test_visibility import ITR_SKIPPING_LEVEL
from ddtrace.ext.test_visibility._test_visibility_base import TestId
from ddtrace.ext.test_visibility._test_visibility_base import TestModuleId
from ddtrace.ext.test_visibility._test_visibility_base import TestSessionId
from ddtrace.ext.test_visibility._test_visibility_base import TestSuiteId
from ddtrace.ext.test_visibility._test_visibility_base import TestVisibilityItemId
from ddtrace.internal import agent
from ddtrace.internal import atexit
from ddtrace.internal import telemetry
from ddtrace.internal.ci_visibility._api_client import AgentlessTestVisibilityAPIClient
from ddtrace.internal.ci_visibility._api_client import EarlyFlakeDetectionSettings
from ddtrace.internal.ci_visibility._api_client import EVPProxyTestVisibilityAPIClient
from ddtrace.internal.ci_visibility._api_client import ITRData
from ddtrace.internal.ci_visibility._api_client import TestManagementSettings
from ddtrace.internal.ci_visibility._api_client import TestProperties
from ddtrace.internal.ci_visibility._api_client import TestVisibilityAPISettings
from ddtrace.internal.ci_visibility._api_client import _TestVisibilityAPIClientBase
from ddtrace.internal.ci_visibility.api._module import TestVisibilityModule
from ddtrace.internal.ci_visibility.api._session import TestVisibilitySession
from ddtrace.internal.ci_visibility.api._session import TestVisibilitySessionSettings
from ddtrace.internal.ci_visibility.api._suite import TestVisibilitySuite
from ddtrace.internal.ci_visibility.api._test import TestVisibilityTest
from ddtrace.internal.ci_visibility.constants import AGENTLESS_DEFAULT_SITE
from ddtrace.internal.ci_visibility.constants import CUSTOM_CONFIGURATIONS_PREFIX
from ddtrace.internal.ci_visibility.constants import EVP_PROXY_AGENT_BASE_PATH
from ddtrace.internal.ci_visibility.constants import EVP_PROXY_AGENT_BASE_PATH_V4
from ddtrace.internal.ci_visibility.constants import EVP_SUBDOMAIN_HEADER_EVENT_VALUE
from ddtrace.internal.ci_visibility.constants import EVP_SUBDOMAIN_HEADER_NAME
from ddtrace.internal.ci_visibility.constants import ITR_CORRELATION_ID_TAG_NAME
from ddtrace.internal.ci_visibility.constants import REQUESTS_MODE
from ddtrace.internal.ci_visibility.constants import SUITE
from ddtrace.internal.ci_visibility.constants import TEST
from ddtrace.internal.ci_visibility.constants import TRACER_PARTIAL_FLUSH_MIN_SPANS
from ddtrace.internal.ci_visibility.constants import UNSUPPORTED_PROVIDER
from ddtrace.internal.ci_visibility.context import CIContextProvider
from ddtrace.internal.ci_visibility.errors import CIVisibilityAuthenticationException
from ddtrace.internal.ci_visibility.errors import CIVisibilityError
from ddtrace.internal.ci_visibility.filters import TraceCiVisibilityFilter
from ddtrace.internal.ci_visibility.git_client import METADATA_UPLOAD_STATUS
from ddtrace.internal.ci_visibility.git_client import CIVisibilityGitClient
from ddtrace.internal.ci_visibility.git_data import GitData
from ddtrace.internal.ci_visibility.git_data import get_git_data_from_tags
from ddtrace.internal.ci_visibility.service_registry import register_ci_visibility_instance
from ddtrace.internal.ci_visibility.service_registry import unregister_ci_visibility_instance
from ddtrace.internal.ci_visibility.utils import _get_test_framework_telemetry_name
from ddtrace.internal.ci_visibility.writer import CIVisibilityEventClient
from ddtrace.internal.ci_visibility.writer import CIVisibilityWriter
from ddtrace.internal.codeowners import Codeowners
from ddtrace.internal.logger import get_logger
from ddtrace.internal.service import Service
from ddtrace.internal.test_visibility._atr_mixins import AutoTestRetriesSettings
from ddtrace.internal.test_visibility._library_capabilities import LibraryCapabilities
from ddtrace.internal.utils.formats import asbool
from ddtrace.settings import IntegrationConfig
from ddtrace.settings._agent import config as agent_config
from ddtrace.trace import Tracer


log = get_logger(__name__)

DEFAULT_TIMEOUT = 15
DEFAULT_ITR_SKIPPABLE_TIMEOUT = 20
UNSUPPORTED = "unsupported"
TELEMETRY_BY_PROVIDER_NAME = {
    "appveyor": "provider:appveyor",
    "azurepipelines": "provider:azp",
    "bitbucket": "provider:bitbucket",
    "buildkite": "provider:buildkite",
    "circleci": "provider:circleci",
    "codefresh": "provider:codefresh",
    "github": "provider:githubactions",
    "gitlab": "provider:gitlab",
    "jenkins": "provider:jenkins",
    "teamcity": "provider:teamcity",
    "travisci": "provider:travisci",
    "bitrise": "provider:bitrise",
    "buddy": "provider:buddyci",
    "awscodepipeline": "provider:aws",
    UNSUPPORTED: UNSUPPORTED_PROVIDER,
}


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


def _get_git_repo() -> Optional[str]:
    # this exists only for the purpose of patching in tests
    return None


def _get_custom_configurations() -> Dict[str, str]:
    custom_configurations = {}
    for tag, value in ddconfig.tags.items():
        if tag.startswith(CUSTOM_CONFIGURATIONS_PREFIX):
            custom_configurations[tag.replace("%s." % CUSTOM_CONFIGURATIONS_PREFIX, "", 1)] = value

    return custom_configurations


def _is_item_itr_skippable(item_id: TestVisibilityItemId, suite_skipping_mode: bool, itr_data: Optional[ITRData]):
    if itr_data is None:
        return False

    if isinstance(item_id, TestSuiteId) and not suite_skipping_mode:
        log.debug("Skipping mode is at test level, but item is a suite: %s", item_id)
        return False

    if isinstance(item_id, TestId) and suite_skipping_mode:
        log.debug("Skipping mode is at suite level, but item is a test: %s", item_id)
        return False
    return item_id in itr_data.skippable_items


class CIVisibilityTracer(Tracer):
    def __init__(self, *args, **kwargs) -> None:
        # Allows for multiple instances of the civis tracer to be created without logging a warning
        super().__init__(*args, **kwargs)


class CIVisibility(Service):
    _instance: Optional["CIVisibility"] = None
    enabled = False

    def __init__(
        self, tracer: Optional[Tracer] = None, config: Optional[IntegrationConfig] = None, service: Optional[str] = None
    ) -> None:
        super().__init__()

        if tracer:
            self.tracer = tracer
        else:
            if asbool(os.getenv("_DD_CIVISIBILITY_USE_CI_CONTEXT_PROVIDER")):
                log.debug("Using DD CI context provider: test traces may be incomplete, telemetry may be inaccurate")
                # Create a new CI tracer, using a specific URL if provided (only useful when testing the tracer itself)
                self.tracer = CIVisibilityTracer()

                env_agent_url = os.getenv("_CI_DD_AGENT_URL")
                if env_agent_url is not None:
                    log.debug("Using _CI_DD_AGENT_URL for CI Visibility tracer: %s", env_agent_url)
                    self.tracer._span_aggregator.writer.intake_url = env_agent_url  # type: ignore[attr-defined]
                self.tracer.context_provider = CIContextProvider()
            else:
                self.tracer = ddtrace.tracer

            # Partial traces are required for ITR to work in suite-level skipping for long test sessions, but we
            # assume that a tracer is already configured if it's been passed in.
            self.tracer._span_aggregator.partial_flush_enabled = True
            self.tracer._span_aggregator.partial_flush_min_spans = TRACER_PARTIAL_FLUSH_MIN_SPANS
            # Tracer.configure(...) sets Tracer.enabled to the global ddconfig._tracing_enabled value
            # (in Tracer._reset(...)). Removing this side-effect causes some CIVisibility tests to fail.
            # This MIGHT be due to the shutdown the global tracer in tests (calling tracer.shutdown()
            # sets tracer.enabled to False and is meant to be an irreversible operation).
            # To avoid breaking CIVisibility, we continue to reset self.enabled here
            # to match the global config. Although not ideal, this is the safest way to refactor the Tracer class
            # without disrupting existing behavior. The CIVisibility team will investigate this further in a future PR.
            self.tracer.enabled = ddconfig._tracing_enabled
            self.tracer._recreate()

        self._api_client: Optional[_TestVisibilityAPIClientBase] = None

        self._configurations = ci._get_runtime_and_os_metadata()
        custom_configurations = _get_custom_configurations()
        if custom_configurations:
            self._configurations["custom"] = custom_configurations

        self._api_key = os.getenv("_CI_DD_API_KEY", os.getenv("DD_API_KEY"))

        self._dd_site = os.getenv("DD_SITE", AGENTLESS_DEFAULT_SITE)
        self.config = config or ddconfig.test_visibility  # type: Optional[IntegrationConfig]
        self._itr_skipping_level: ITR_SKIPPING_LEVEL = ddconfig.test_visibility.itr_skipping_level
        self._itr_skipping_ignore_parameters: bool = ddconfig.test_visibility._itr_skipping_ignore_parameters
        if not isinstance(ddconfig.test_visibility.itr_skipping_level, ITR_SKIPPING_LEVEL):
            log.warning(
                "itr_skipping_level should be of type %s but is of type %s, defaulting to %s",
                ITR_SKIPPING_LEVEL,
                type(ddconfig.test_visibility.itr_skipping_level),
                ITR_SKIPPING_LEVEL.TEST.name,
            )
            self._itr_skipping_level = ITR_SKIPPING_LEVEL.TEST
        self._suite_skipping_mode = ddconfig.test_visibility.itr_skipping_level == ITR_SKIPPING_LEVEL.SUITE
        self._tags: Dict[str, str] = ci.tags(cwd=_get_git_repo())
        self._is_auto_injected = bool(os.getenv("DD_CIVISIBILITY_AUTO_INSTRUMENTATION_PROVIDER", ""))
        self._service = service
        self._codeowners = None
        self._root_dir = None
        self._should_upload_git_metadata = True
        self._itr_meta: Dict[str, Any] = {}
        self._itr_data: Optional[ITRData] = None
        self._known_test_ids: Set[TestId] = set()
        self._test_properties: Dict[TestId, TestProperties] = {}

        self._session: Optional[TestVisibilitySession] = None

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

        self._git_data: GitData = get_git_data_from_tags(self._tags)

        self._dd_env = os.getenv("_CI_DD_ENV", ddconfig.env)
        dd_env_msg = ""

        if ddconfig._ci_visibility_agentless_enabled:
            # In agentless mode, normalize an unset env to none (this is already done by the backend in most cases, so
            # it does not override default behavior)
            if self._dd_env is None:
                self._dd_env = "none"
                dd_env_msg = " (not set in environment)"
            if not self._api_key:
                raise EnvironmentError(
                    "DD_CIVISIBILITY_AGENTLESS_ENABLED is set, but DD_API_KEY is not set, so ddtrace "
                    "cannot be initialized."
                )
            requests_mode_str = "agentless"
            self._requests_mode = REQUESTS_MODE.AGENTLESS_EVENTS
            self._api_client = AgentlessTestVisibilityAPIClient(
                self._itr_skipping_level,
                self._git_data,
                self._configurations,
                self._api_key,
                self._dd_site,
                ddconfig._ci_visibility_agentless_url if ddconfig._ci_visibility_agentless_url else None,
                self._service,
                self._dd_env,
            )
        elif evp_proxy_base_url := self._agent_evp_proxy_base_url():
            # In EVP-proxy cases, if an env is not provided, we need to get the agent's default env in order to make
            # the correct decision:
            if self._dd_env is None:
                self._dd_env = self._agent_get_default_env()
                dd_env_msg = " (default environment provided by agent)"
            self._requests_mode = REQUESTS_MODE.EVP_PROXY_EVENTS
            requests_mode_str = "EVP Proxy"
            self._api_client = EVPProxyTestVisibilityAPIClient(
                self._itr_skipping_level,
                self._git_data,
                self._configurations,
                self.tracer._agent_url or agent_config.trace_agent_url,
                self._service,
                self._dd_env,
                evp_proxy_base_url=evp_proxy_base_url,
            )
        else:
            requests_mode_str = "APM (some features will be disabled)"
            self._requests_mode = REQUESTS_MODE.TRACES
            self._should_upload_git_metadata = False

        if self._should_upload_git_metadata:
            self._git_client = CIVisibilityGitClient(
                api_key=self._api_key or "", requests_mode=self._requests_mode, tracer=self.tracer
            )
            self._git_client.upload_git_metadata(cwd=_get_git_repo())

        self._api_settings = self._check_enabled_features()

        self._collect_coverage_enabled = self._should_collect_coverage(self._api_settings.coverage_enabled)

        self._configure_writer(coverage_enabled=self._collect_coverage_enabled, url=self.tracer._agent_url)

        log.info("Service: %s (env: %s%s)", self._service, self._dd_env, dd_env_msg)
        log.info("Requests mode: %s", requests_mode_str)
        log.info("Git metadata upload enabled: %s", self._should_upload_git_metadata)
        log.info("API-provided settings: coverage collection: %s", self._api_settings.coverage_enabled)
        log.info(
            "API-provided settings: Intelligent Test Runner: %s, test skipping: %s",
            self._api_settings.itr_enabled,
            self._api_settings.skipping_enabled,
        )
        log.info(
            "API-provided settings: Early Flake Detection enabled: %s",
            self._api_settings.early_flake_detection.enabled,
        )
        log.info(
            "API-provided settings: Known Tests enabled: %s",
            self._api_settings.known_tests_enabled,
        )
        log.info("API-provided settings: Auto Test Retries enabled: %s", self._api_settings.flaky_test_retries_enabled)
        log.info("Detected configurations: %s", str(self._configurations))

        try:
            self._codeowners = Codeowners(cwd=self._tags.get(ci.WORKSPACE_PATH))
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
        return True

    def _check_enabled_features(self) -> TestVisibilityAPISettings:
        _error_return_value = TestVisibilityAPISettings()

        if not self._api_client:
            log.warning("API client not initialized, disabling coverage collection and test skipping")
            return _error_return_value

        try:
            settings = self._api_client.fetch_settings()
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
                settings = self._api_client.fetch_settings()
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

    def _configure_writer(
        self, coverage_enabled: bool = False, requests_mode: Optional[REQUESTS_MODE] = None, url: Optional[str] = None
    ) -> None:
        writer = None
        if requests_mode is None:
            requests_mode = self._requests_mode

        if requests_mode == REQUESTS_MODE.AGENTLESS_EVENTS:
            headers = {"dd-api-key": self._api_key or ""}
            writer = CIVisibilityWriter(
                headers=headers,
                coverage_enabled=coverage_enabled,
                itr_suite_skipping_mode=self._suite_skipping_mode,
                use_gzip=True,
            )
        elif requests_mode == REQUESTS_MODE.EVP_PROXY_EVENTS:
            writer = CIVisibilityWriter(
                intake_url=url or agent_config.trace_agent_url,
                headers={EVP_SUBDOMAIN_HEADER_NAME: EVP_SUBDOMAIN_HEADER_EVENT_VALUE},
                use_evp=True,
                coverage_enabled=coverage_enabled,
                itr_suite_skipping_mode=self._suite_skipping_mode,
                use_gzip=self._is_gzip_supported_by_agent(),
            )
        if writer is not None:
            self.tracer._span_aggregator.writer = writer
            self.tracer._recreate()

    def _agent_evp_proxy_base_url(self) -> Optional[str]:
        if asbool(os.getenv("_DD_CIVISIBILITY_DISABLE_EVP_PROXY")):
            return None

        try:
            info = agent.info(self.tracer._agent_url)
        except Exception:
            return None

        if info:
            endpoints = info.get("endpoints", [])
            if endpoints and any(EVP_PROXY_AGENT_BASE_PATH_V4 in endpoint for endpoint in endpoints):
                return EVP_PROXY_AGENT_BASE_PATH_V4
            if endpoints and any(EVP_PROXY_AGENT_BASE_PATH in endpoint for endpoint in endpoints):
                return EVP_PROXY_AGENT_BASE_PATH
        return None

    def _is_gzip_supported_by_agent(self) -> bool:
        return self._agent_evp_proxy_base_url() == EVP_PROXY_AGENT_BASE_PATH_V4

    def _agent_get_default_env(self) -> Optional[str]:
        try:
            info = agent.info(self.tracer._agent_url)
        except Exception:
            return "none"

        if info:
            return info.get("config", {}).get("default_env", "none")
        return "none"

    @classmethod
    def is_itr_enabled(cls) -> bool:
        # cls.enabled guarantees _instance is not None
        if not cls.enabled or cls._instance is None:
            return False

        if not ddconfig._ci_visibility_intelligent_testrunner_enabled:
            log.debug("Intelligent Test Runner is disabled by environment variable")
            return False

        return cls._instance._api_settings.itr_enabled

    @classmethod
    def test_skipping_enabled(cls) -> bool:
        if (
            not cls.enabled
            or cls._instance is None
            or asbool(os.getenv("_DD_CIVISIBILITY_ITR_PREVENT_TEST_SKIPPING", default=False))
        ):
            return False
        return cls._instance._api_settings.skipping_enabled

    @classmethod
    def is_known_tests_enabled(cls) -> bool:
        if cls._instance is None:
            return False
        return cls._instance._api_settings.known_tests_enabled

    @classmethod
    def is_efd_enabled(cls) -> bool:
        if cls._instance is None:
            return False
        return (
            cls._instance._api_settings.known_tests_enabled  # Known Tests Enabled takes precedence over EFD
            and cls._instance._api_settings.early_flake_detection.enabled
            and ddconfig._test_visibility_early_flake_detection_enabled
        )

    @classmethod
    def is_atr_enabled(cls) -> bool:
        if cls._instance is None:
            return False
        return cls._instance._api_settings.flaky_test_retries_enabled and asbool(
            os.getenv("DD_CIVISIBILITY_FLAKY_RETRY_ENABLED", default=True)
        )

    @classmethod
    def is_test_management_enabled(cls) -> bool:
        if cls._instance is None:
            return False
        return cls._instance._api_settings.test_management.enabled and asbool(
            os.getenv("DD_TEST_MANAGEMENT_ENABLED", default=True)
        )

    @classmethod
    def should_collect_coverage(cls) -> bool:
        if cls._instance is None:
            return False
        return cls._instance._api_settings.coverage_enabled or asbool(
            os.getenv("_DD_CIVISIBILITY_ITR_FORCE_ENABLE_COVERAGE", default=False)
        )

    def _fetch_tests_to_skip(self) -> None:
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

        try:
            if self._api_client is None:
                return
            self._itr_data = self._api_client.fetch_skippable_items(
                ignore_test_parameters=self._itr_skipping_ignore_parameters
            )
            if self._itr_data is not None and self._itr_data.correlation_id is not None:
                self._itr_meta[ITR_CORRELATION_ID_TAG_NAME] = self._itr_data.correlation_id
        except Exception:  # noqa: E722
            log.debug("Error fetching skippable items", exc_info=True)

    def _fetch_known_tests(self) -> Optional[Set[TestId]]:
        try:
            if self._api_client is not None:
                return self._api_client.fetch_known_tests()
            log.warning("API client not initialized, cannot fetch unique tests")
        except Exception:
            log.debug("Error fetching unique tests", exc_info=True)
        return None

    def _fetch_test_management_tests(self) -> Optional[Dict[TestId, TestProperties]]:
        try:
            if self._api_client is not None:
                return self._api_client.fetch_test_management_tests()
            log.warning("API client not initialized, cannot fetch tests from Test Management")
        except Exception:
            log.debug("Error fetching unique tests", exc_info=True)
        return None

    def _should_skip_path(self, path: str, name: str, test_skipping_mode: Optional[str] = None) -> bool:
        """This method supports legacy usage of the CIVisibility service and should be removed

        The conversion of path to TestId or SuiteId is redundant and absent from the new way of getting item
        skipping status. This method has been updated to look for item_ids in a way that matches the previous behavior,
        including questionable use of os.path.relpath.

        Note that in this legacy mode, test parameters are ignored.
        """
        if self._itr_data is None:
            return False
        if test_skipping_mode is None:
            _test_skipping_mode = SUITE if self._suite_skipping_mode else TEST
        else:
            _test_skipping_mode = test_skipping_mode

        module_path, _, suite_name = os.path.relpath(path).rpartition("/")
        module_name = module_path.replace("/", ".")
        suite_id = TestSuiteId(TestModuleId(module_name), suite_name)

        item_id = suite_id if _test_skipping_mode == SUITE else TestId(suite_id, name)

        return item_id in self._itr_data.skippable_items

    @classmethod
    def enable(cls, tracer=None, config=None, service=None) -> None:
        log.debug("Enabling %s", cls.__name__)
        if cls._instance is not None:
            log.debug("%s already enabled", cls.__name__)
            return

        if ddconfig._ci_visibility_agentless_enabled:
            if not os.getenv("_CI_DD_API_KEY", os.getenv("DD_API_KEY")):
                log.critical(
                    "%s disabled: environment variable DD_CIVISIBILITY_AGENTLESS_ENABLED is true but"
                    " DD_API_KEY is not set",
                    cls.__name__,
                )
                cls.enabled = False
                return

        try:
            cls._instance = cls(tracer=tracer, config=config, service=service)
            # Register with service registry for other modules to access
            register_ci_visibility_instance(cls._instance)

        except CIVisibilityAuthenticationException:
            log.warning("Authentication error, disabling CI Visibility, please check Datadog API key")
            cls.enabled = False
            return

        cls.enabled = True

        cls._instance.start()
        atexit.register(cls.disable)

        log.debug("%s enabled", cls.__name__)
        log.info(
            "Final settings: coverage collection: %s, "
            "test skipping: %s, "
            "Early Flake Detection: %s, "
            "Auto Test Retries: %s, "
            "Flaky Test Management: %s, "
            "Known Tests: %s",
            cls._instance._collect_coverage_enabled,
            cls._instance.test_skipping_enabled(),
            cls._instance.is_efd_enabled(),
            cls._instance.is_atr_enabled(),
            cls._instance.is_test_management_enabled(),
            cls._instance.is_known_tests_enabled(),
        )

    @classmethod
    def disable(cls) -> None:
        if cls._instance is None:
            log.debug("%s not enabled", cls.__name__)
            return
        log.debug("Disabling %s", cls.__name__)
        atexit.unregister(cls.disable)

        # Unregister from service registry first
        unregister_ci_visibility_instance()

        cls._instance.stop()
        cls._instance = None
        cls.enabled = False

        telemetry.telemetry_writer.periodic(force_flush=True)

        log.debug("%s disabled", cls.__name__)

    def _start_service(self) -> None:
        tracer_filters = self.tracer._span_aggregator.user_processors
        if not any(isinstance(tracer_filter, TraceCiVisibilityFilter) for tracer_filter in tracer_filters):
            tracer_filters += [TraceCiVisibilityFilter(self._tags, self._service)]  # type: ignore[arg-type]
            self.tracer.configure(trace_processors=tracer_filters)

        def _task_fetch_tests_to_skip():
            if self.test_skipping_enabled():
                self._fetch_tests_to_skip()
                if self._itr_data is None:
                    log.warning("Failed to fetch skippable items, no tests will be skipped.")
                    return
                log.info("Intelligent Test Runner skipping level: %s", "suite" if self._suite_skipping_mode else "test")
                log.info("Skippable items fetched: %s", len(self._itr_data.skippable_items))
                log.info("ITR correlation ID: %s", self._itr_data.correlation_id)

        def _task_fetch_known_tests():
            if CIVisibility.is_known_tests_enabled():
                known_test_ids = self._fetch_known_tests()
                if known_test_ids is None:
                    log.warning("Failed to fetch known tests for Early Flake Detection")
                else:
                    self._known_test_ids = known_test_ids
                    log.info("Known tests fetched for Early Flake Detection: %s", len(self._known_test_ids))
            else:
                if (
                    self._api_settings.early_flake_detection.enabled
                    and not ddconfig._test_visibility_early_flake_detection_enabled
                ):
                    log.warning(
                        "Early Flake Detection is enabled by API but disabled by "
                        "DD_TEST_VISIBILITY_EARLY_FLAKE_DETECTION_ENABLED environment variable"
                    )

        def _task_fetch_test_management_tests():
            if self._api_settings.test_management.enabled:
                test_properties = self._fetch_test_management_tests()
                if test_properties is None:
                    log.warning("Failed to fetch quarantined tests from Test Management")
                else:
                    self._test_properties = test_properties

        with ThreadPoolExecutor() as pool:
            pool.submit(_task_fetch_tests_to_skip)
            pool.submit(_task_fetch_known_tests)
            pool.submit(_task_fetch_test_management_tests)

        if self._api_settings.flaky_test_retries_enabled and not asbool(
            os.environ.get("DD_CIVISIBILITY_FLAKY_RETRY_ENABLED", True)
        ):
            log.warning(
                "Auto Test Retries is enabled by API but disabled by "
                "DD_CIVISIBILITY_FLAKY_RETRY_ENABLED environment variable"
            )

    def _stop_service(self) -> None:
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
        except Exception:  # noqa: E722
            log.debug("Error setting codeowners for %s", location, exc_info=True)

    @classmethod
    def add_session(cls, session: TestVisibilitySession):
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
        item_id: TestVisibilityItemId,
    ):
        if cls._instance is None:
            error_msg = "CI Visibility is not enabled"
            log.warning(error_msg)
            raise CIVisibilityError(error_msg)
        if isinstance(item_id, TestSessionId):
            return cls.get_session()
        if isinstance(item_id, TestModuleId):
            return cls.get_module_by_id(item_id)
        if isinstance(item_id, TestSuiteId):
            return cls.get_suite_by_id(item_id)
        if isinstance(item_id, TestId):
            return cls.get_test_by_id(item_id)
        error_msg = f"Unknown item id type: {type(item_id)}"
        log.warning(error_msg)
        raise CIVisibilityError(error_msg)

    @classmethod
    def get_session(cls) -> TestVisibilitySession:
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
    def get_module_by_id(cls, module_id: TestModuleId) -> TestVisibilityModule:
        if cls._instance is None:
            error_msg = "CI Visibility is not enabled"
            log.warning(error_msg)
            raise CIVisibilityError(error_msg)
        return cls.get_session().get_child_by_id(module_id)

    @classmethod
    def get_suite_by_id(cls, suite_id: TestSuiteId) -> TestVisibilitySuite:
        if cls._instance is None:
            error_msg = "CI Visibility is not enabled"
            log.warning(error_msg)
            raise CIVisibilityError(error_msg)
        return cls.get_module_by_id(suite_id.parent_id).get_child_by_id(suite_id)

    @classmethod
    def get_test_by_id(cls, test_id: TestId) -> TestVisibilityTest:
        if cls._instance is None:
            error_msg = "CI Visibility is not enabled"
            log.warning(error_msg)
            raise CIVisibilityError(error_msg)
        return cls.get_suite_by_id(test_id.parent_id).get_child_by_id(test_id)

    @classmethod
    def get_session_settings(cls) -> TestVisibilitySessionSettings:
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

    def get_tracer(self) -> Optional[Tracer]:
        return self.tracer

    def get_service(self) -> Optional[str]:
        return self._service

    def get_codeowners(self) -> Optional[Codeowners]:
        return self._codeowners

    def get_efd_api_settings(self) -> Optional[EarlyFlakeDetectionSettings]:
        if self._api_settings is None:
            return None
        return self._api_settings.early_flake_detection

    def get_atr_api_settings(self) -> Optional[AutoTestRetriesSettings]:
        if self._api_settings is None:
            return None

        if self._api_settings.flaky_test_retries_enabled:
            # NOTE: this is meant to come from integration settings but current plans to rewrite how integration
            # settings are defined make it better for this logic to be temporarily defined here.

            # defaults
            max_retries = 5
            max_session_total_retries = 1000

            env_max_retries = os.environ.get("DD_CIVISIBILITY_FLAKY_RETRY_COUNT")
            if env_max_retries is not None:
                try:
                    max_retries = int(env_max_retries)
                except ValueError:
                    log.warning(
                        "Failed to parse DD_CIVISIBILITY_FLAKY_RETRY_COUNT, using default value: %s", max_retries
                    )

            env_max_session_total_retries = os.environ.get("DD_CIVISIBILITY_TOTAL_FLAKY_RETRY_COUNT")
            if env_max_session_total_retries is not None:
                try:
                    max_session_total_retries = int(env_max_session_total_retries)
                except ValueError:
                    log.warning(
                        "Failed to parse DD_CIVISIBILITY_TOTAL_FLAKY_RETRY_COUNT, using default value: %s",
                        max_session_total_retries,
                    )

            return AutoTestRetriesSettings(
                enabled=True, max_retries=max_retries, max_session_total_retries=max_session_total_retries
            )

        return None

    def get_test_management_api_settings(self) -> Optional[TestManagementSettings]:
        if self._api_settings is None:
            return None
        return self._api_settings.test_management

    def get_workspace_path(self) -> Optional[str]:
        return self._tags.get(ci.WORKSPACE_PATH)

    def is_item_itr_skippable(self, item_id: TestVisibilityItemId) -> bool:
        return _is_item_itr_skippable(item_id, self._suite_skipping_mode, self._itr_data)

    def is_unknown_ci(self) -> bool:
        return self._tags.get(ci.PROVIDER_NAME) is None

    def ci_provider_name_for_telemetry(self) -> str:
        return TELEMETRY_BY_PROVIDER_NAME.get(self._tags.get(ci.PROVIDER_NAME, UNSUPPORTED), UNSUPPORTED_PROVIDER)

    def is_auto_injected(self) -> bool:
        return self._is_auto_injected

    def _get_ci_visibility_event_client(self) -> Optional[CIVisibilityEventClient]:
        writer = self.tracer._span_aggregator.writer
        if isinstance(writer, CIVisibilityWriter):
            for client in writer._clients:
                if isinstance(client, CIVisibilityEventClient):
                    return client

        return None

    def set_test_session_name(self, test_command: str) -> None:
        client = self._get_ci_visibility_event_client()
        if not client:
            log.debug("Not setting test session name because no CIVisibilityEventClient is active")
            return

        if ddconfig._test_session_name:
            test_session_name = ddconfig._test_session_name
        else:
            job_name = self._tags.get(ci.JOB_NAME)
            test_session_name = f"{job_name}-{test_command}" if job_name else test_command

        log.debug("Setting test session name: %s", test_session_name)
        client.set_test_session_name(test_session_name)

    def set_library_capabilities(self, capabilities: LibraryCapabilities) -> None:
        client = self._get_ci_visibility_event_client()
        if not client:
            log.debug("Not setting library capabilities because no CIVisibilityEventClient is active")
            return
        client.set_metadata("test", capabilities.tags())

    def get_ci_tags(self):
        return self._tags

    def get_dd_env(self):
        return self._dd_env

    def is_known_test(self, test_id: Union[TestId, TestId]) -> bool:
        # The assumption that we were not able to fetch unique tests properly if the length is 0 is acceptable
        # because the current EFD usage would cause the session to be faulty even if the query was successful but
        # not unique tests exist. In this case, we assume all tests are unique.
        if len(self._known_test_ids) == 0:
            return True

        return test_id in self._known_test_ids

    def get_test_properties(self, test_id: TestId) -> Optional[TestProperties]:
        return self._test_properties.get(test_id)


def _requires_civisibility_enabled(func: Callable) -> Callable:
    def wrapper(*args, **kwargs) -> Any:
        if not CIVisibility.enabled:
            log.warning("CI Visibility is not enabled")
            raise CIVisibilityError("CI Visibility is not enabled")
        return func(*args, **kwargs)

    return wrapper


@_requires_civisibility_enabled
def on_discover_session(
    test_command,
    reject_duplicates,
    test_framework,
    test_framework_version,
    session_operation_name,
    module_operation_name,
    suite_operation_name,
    test_operation_name,
    root_dir,
) -> None:
    log.debug("Handling session discovery")

    # _requires_civisibility_enabled prevents us from getting here, but this makes type checkers happy
    instance = CIVisibility.get_instance()
    test_service = instance.get_service()
    tracer = instance.get_tracer()

    if tracer is None or test_service is None:
        error_msg = "Tracer or test service is None"
        log.warning(error_msg)
        raise CIVisibilityError(error_msg)

    # If we're not provided a root directory, try and extract it from workspace, defaulting to CWD
    workspace_path = root_dir or Path(instance.get_workspace_path() or os.getcwd())

    # Prevent high cardinality of test framework telemetry tag by matching with known frameworks
    test_framework_telemetry_name = _get_test_framework_telemetry_name(test_framework)

    efd_api_settings = instance.get_efd_api_settings()
    if efd_api_settings is None or not instance.is_efd_enabled():
        efd_api_settings = EarlyFlakeDetectionSettings()

    atr_api_settings = instance.get_atr_api_settings()
    if atr_api_settings is None or not CIVisibility.is_atr_enabled():
        atr_api_settings = AutoTestRetriesSettings()

    test_management_api_settings = instance.get_test_management_api_settings()
    if test_management_api_settings is None or not instance.is_test_management_enabled():
        test_management_api_settings = TestManagementSettings()

    session_settings = TestVisibilitySessionSettings(
        tracer=tracer,
        test_service=test_service,
        test_command=test_command,
        reject_duplicates=reject_duplicates,
        test_framework=test_framework,
        test_framework_metric_name=test_framework_telemetry_name,
        test_framework_version=test_framework_version,
        session_operation_name=session_operation_name,
        module_operation_name=module_operation_name,
        suite_operation_name=suite_operation_name,
        test_operation_name=test_operation_name,
        workspace_path=workspace_path,
        is_unsupported_ci=instance.is_unknown_ci(),
        itr_enabled=instance.is_itr_enabled(),
        itr_test_skipping_enabled=instance.test_skipping_enabled(),
        itr_test_skipping_level=instance._itr_skipping_level,
        itr_correlation_id=instance._itr_meta.get(ITR_CORRELATION_ID_TAG_NAME, ""),
        coverage_enabled=instance.should_collect_coverage(),
        known_tests_enabled=instance.is_known_tests_enabled(),
        efd_settings=efd_api_settings,
        atr_settings=atr_api_settings,
        test_management_settings=test_management_api_settings,
        ci_provider_name=instance.ci_provider_name_for_telemetry(),
        is_auto_injected=instance.is_auto_injected(),
    )

    session = TestVisibilitySession(
        session_settings,
    )

    CIVisibility.add_session(session)
    instance.set_test_session_name(test_command=test_command)
