from abc import ABC
from abc import abstractmethod
from dataclasses import dataclass
from enum import Enum
from enum import auto
import json
import os
from pathlib import Path
import re
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional
from typing import Set
from typing import Union
from urllib import parse

from ddtrace import config as ddconfig
from ddtrace.ext import ci
from ddtrace.ext import test
from ddtrace.ext.test_visibility import ITR_SKIPPING_LEVEL
from ddtrace.ext.test_visibility._test_visibility_base import TestSessionId
from ddtrace.ext.test_visibility._test_visibility_base import TestVisibilityItemId
from ddtrace.ext.test_visibility.api import Test
from ddtrace.ext.test_visibility.api import TestBase
from ddtrace.ext.test_visibility.api import TestId
from ddtrace.ext.test_visibility.api import TestModule
from ddtrace.ext.test_visibility.api import TestModuleId
from ddtrace.ext.test_visibility.api import TestSession
from ddtrace.ext.test_visibility.api import TestStatus
from ddtrace.ext.test_visibility.api import TestSuite
from ddtrace.ext.test_visibility.api import TestSuiteId
from ddtrace.internal import agent
from ddtrace.internal import atexit
from ddtrace.internal import core
from ddtrace.internal import telemetry
from ddtrace.internal.agent import get_connection
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
from ddtrace.internal.ci_visibility.api_client import ApiClientFactory
from ddtrace.internal.ci_visibility.constants import AGENTLESS_DEFAULT_SITE
from ddtrace.internal.ci_visibility.constants import CUSTOM_CONFIGURATIONS_PREFIX
from ddtrace.internal.ci_visibility.constants import EVP_PROXY_AGENT_BASE_PATH
from ddtrace.internal.ci_visibility.constants import ITR_CORRELATION_ID_TAG_NAME
from ddtrace.internal.ci_visibility.constants import REQUESTS_MODE
from ddtrace.internal.ci_visibility.constants import SUITE
from ddtrace.internal.ci_visibility.constants import TEST
from ddtrace.internal.ci_visibility.constants import UNSUPPORTED_PROVIDER
from ddtrace.internal.ci_visibility.errors import CIVisibilityAuthenticationException
from ddtrace.internal.ci_visibility.errors import CIVisibilityError
from ddtrace.internal.ci_visibility.filters import TraceCiVisibilityFilter
from ddtrace.internal.ci_visibility.git import CIVisibilityGitClient
from ddtrace.internal.ci_visibility.git_client import CIVisibilityGitClient
from ddtrace.internal.ci_visibility.git_data import GitData
from ddtrace.internal.ci_visibility.git_data import get_git_data_from_tags
from ddtrace.internal.ci_visibility.telemetry import enable_telemetry
from ddtrace.internal.ci_visibility.utils import _get_test_framework_telemetry_name
from ddtrace.internal.ci_visibility.utils import is_pytest_xdist_worker
from ddtrace.internal.ci_visibility.writer import CIVisibilityEventClient
from ddtrace.internal.ci_visibility.writer import CIVisibilityWriter
from ddtrace.internal.codeowners import Codeowners
from ddtrace.internal.logger import get_logger
from ddtrace.internal.runtime import RuntimeWorker
from ddtrace.internal.service import Service
from ddtrace.internal.test_visibility._atr_mixins import ATRTestMixin
from ddtrace.internal.test_visibility._atr_mixins import AutoTestRetriesSettings
from ddtrace.internal.test_visibility._attempt_to_fix_mixins import AttemptToFixTestMixin
from ddtrace.internal.test_visibility._benchmark_mixin import BenchmarkTestMixin
from ddtrace.internal.test_visibility._efd_mixins import EFDTestMixin
from ddtrace.internal.test_visibility._efd_mixins import EFDTestStatus
from ddtrace.internal.test_visibility._internal_item_ids import InternalTestId
from ddtrace.internal.test_visibility._itr_mixins import ITRMixin
from ddtrace.internal.test_visibility._library_capabilities import LibraryCapabilities
from ddtrace.internal.test_visibility.api import InternalTest
from ddtrace.internal.test_visibility.coverage_lines import CoverageLines
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.http import verify_url
from ddtrace.internal.writer.writer import Response
from ddtrace.settings import Config
from ddtrace.settings import IntegrationConfig
from ddtrace.settings import _config as ddconfig
from ddtrace.trace import Span
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


def _get_git_repo() -> Optional[str]:
    # this exists only for the purpose of patching in tests
    return None


def _get_custom_configurations() -> Dict[str, str]:
    custom_configurations = {}
    for tag, value in ddconfig.tags.items():
        if tag.startswith(CUSTOM_CONFIGURATIONS_PREFIX):
            custom_configurations[tag.replace("%s." % CUSTOM_CONFIGURATIONS_PREFIX, "", 1)] = value

    return custom_configurations


class CIVisibilityTracer(Tracer):
    def __init__(self, *args, **kwargs) -> None:
        # Allows for multiple instances of the civis tracer to be created without logging a warning
        super().__init__(*args, **kwargs)


class RequestMode(Enum):
    """Enum representing the different request modes for CI Visibility"""
    AGENTLESS = auto()
    EVP_PROXY = auto()
    TRACES = auto()


@dataclass
class EnvConfig:
    """Configuration extracted from environment variables"""
    api_key: Optional[str]
    dd_site: str
    dd_env: Optional[str]
    agentless_url: Optional[str]
    env_message: str = ""
    
    @classmethod
    def from_environment(cls) -> 'EnvConfig':
        """Extract configuration from environment variables"""
        api_key = os.getenv("_CI_DD_API_KEY", os.getenv("DD_API_KEY"))
        dd_site = os.getenv("DD_SITE", AGENTLESS_DEFAULT_SITE)
        dd_env = os.getenv("_CI_DD_ENV", ddconfig.env)
        agentless_url = ddconfig._ci_visibility_agentless_url
        
        return cls(
            api_key=api_key,
            dd_site=dd_site,
            dd_env=dd_env,
            agentless_url=agentless_url
        )


class ClientStrategy(ABC):
    """Abstract strategy for configuring API clients"""
    @abstractmethod
    def create_api_client(
        self, 
        itr_skipping_level: ITR_SKIPPING_LEVEL,
        git_data: GitData,
        configurations: Dict[str, Any],
        service: Optional[str],
        tracer: Tracer,
        env_config: EnvConfig
    ) -> _TestVisibilityAPIClientBase:
        """Create an API client for this strategy"""
        pass

    @abstractmethod
    def get_request_mode(self) -> REQUESTS_MODE:
        """Get the request mode for this strategy"""
        pass
    
    @abstractmethod
    def should_upload_git_metadata(self) -> bool:
        """Whether this strategy requires git metadata uploads"""
        pass
    
    @abstractmethod
    def get_mode_name(self) -> str:
        """Get a human-readable name for this mode"""
        pass


class AgentlessClientStrategy(ClientStrategy):
    """Strategy for agentless mode"""
    def create_api_client(
        self, 
        itr_skipping_level: ITR_SKIPPING_LEVEL,
        git_data: GitData,
        configurations: Dict[str, Any],
        service: Optional[str],
        tracer: Tracer,
        env_config: EnvConfig
    ) -> _TestVisibilityAPIClientBase:
        # Normalize environment to 'none' if not set
        dd_env = env_config.dd_env
        if dd_env is None:
            dd_env = "none"
            env_config.env_message = " (not set in environment)"
            
        return AgentlessTestVisibilityAPIClient(
            itr_skipping_level,
            git_data,
            configurations,
            env_config.api_key,
            env_config.dd_site,
            env_config.agentless_url,
            service,
            dd_env,
        )
    
    def get_request_mode(self) -> REQUESTS_MODE:
        return REQUESTS_MODE.AGENTLESS_EVENTS
    
    def should_upload_git_metadata(self) -> bool:
        return True
        
    def get_mode_name(self) -> str:
        return "agentless"


class EVPProxyClientStrategy(ClientStrategy):
    """Strategy for EVP proxy mode"""
    def create_api_client(
        self, 
        itr_skipping_level: ITR_SKIPPING_LEVEL,
        git_data: GitData,
        configurations: Dict[str, Any],
        service: Optional[str],
        tracer: Tracer,
        env_config: EnvConfig
    ) -> _TestVisibilityAPIClientBase:
        # Get default env from agent if not provided
        dd_env = env_config.dd_env
        if dd_env is None:
            dd_env = self._get_agent_default_env(tracer)
            env_config.env_message = " (default environment provided by agent)"
            
        return EVPProxyTestVisibilityAPIClient(
            itr_skipping_level,
            git_data,
            configurations,
            tracer._agent_url,
            service,
            dd_env,
        )

    def get_request_mode(self) -> REQUESTS_MODE:
        return REQUESTS_MODE.EVP_PROXY_EVENTS
    
    def should_upload_git_metadata(self) -> bool:
        return True
        
    def get_mode_name(self) -> str:
        return "EVP Proxy"
        
    def _get_agent_default_env(self, tracer: Tracer) -> str:
        """Get the default environment from the agent"""
        try:
            info = agent.info(tracer._agent_url)
        except Exception:
            return "none"

        if info:
            return info.get("config", {}).get("default_env", "none")
        return "none"


class TracesClientStrategy(ClientStrategy):
    """Strategy for traces mode (fallback)"""
    def create_api_client(
        self, 
        itr_skipping_level: ITR_SKIPPING_LEVEL,
        git_data: GitData,
        configurations: Dict[str, Any],
        service: Optional[str],
        tracer: Tracer,
        env_config: EnvConfig
    ) -> _TestVisibilityAPIClientBase:
        # No client creation needed for traces mode
        return None
    
    def get_request_mode(self) -> REQUESTS_MODE:
        return REQUESTS_MODE.TRACES
    
    def should_upload_git_metadata(self) -> bool:
        return False
        
    def get_mode_name(self) -> str:
        return "APM (some features will be disabled)"


class ClientStrategyFactory:
    """Factory for creating the appropriate client strategy"""
    @staticmethod
    def create_strategy(tracer: Tracer) -> ClientStrategy:
        """Create the appropriate client strategy based on configuration"""
        # Check for agentless mode first (highest priority)
        if ddconfig._ci_visibility_agentless_enabled:
            return AgentlessClientStrategy()
            
        # Check for EVP proxy availability next
        if ClientStrategyFactory._is_evp_proxy_available(tracer):
            return EVPProxyClientStrategy()
            
        # Fall back to traces mode
        return TracesClientStrategy()
    
    @staticmethod
    def _is_evp_proxy_available(tracer: Tracer) -> bool:
        """Check if EVP proxy is available"""
        try:
            info = agent.info(tracer._agent_url)
        except Exception:
            info = None

        if info:
            endpoints = info.get("endpoints", [])
            if endpoints and any(EVP_PROXY_AGENT_BASE_PATH in endpoint for endpoint in endpoints):
                return True
        return False


class CIVisibility(Service):
    _instance: Optional["CIVisibility"] = None
    enabled = False

    def __init__(
        self, tracer: Optional[Tracer] = None, config: Optional[IntegrationConfig] = None, service: Optional[str] = None
    ) -> None:
        super().__init__()

        # Initialize tracer
        self.tracer = self._init_tracer(tracer)
        
        # Initialize basic configuration
        self._service = service
        self.config = config or ddconfig.test_visibility
        self._initialize_itr_settings()
        
        # Extract git data and tags
        self._tags: Dict[str, str] = ci.tags(cwd=_get_git_repo())
        self._git_data: GitData = get_git_data_from_tags(self._tags)
        
        # Extract custom configurations
        self._configurations = ci._get_runtime_and_os_metadata()
        custom_configurations = _get_custom_configurations()
        if custom_configurations:
            self._configurations["custom"] = custom_configurations
            
        # Initialize instance variables
        self._is_auto_injected = bool(os.getenv("DD_CIVISIBILITY_AUTO_INSTRUMENTATION_PROVIDER", ""))
        self._codeowners = None
        self._root_dir = None
        self._itr_meta: Dict[str, Any] = {}
        self._itr_data: Optional[ITRData] = None
        self._unique_test_ids: Set[InternalTestId] = set()
        self._test_properties: Dict[InternalTestId, TestProperties] = {}
        self._session: Optional[TestVisibilitySession] = None
        
        # Resolve service name if not provided
        self._resolve_service_name()

        # Create environment configuration
        env_config = EnvConfig.from_environment()
        
        # Validate API key for agentless mode
        if ddconfig._ci_visibility_agentless_enabled and not env_config.api_key:
            raise EnvironmentError(
                "DD_CIVISIBILITY_AGENTLESS_ENABLED is set, but DD_API_KEY is not set, so ddtrace "
                "cannot be initialized."
            )
        
        # Create client strategy
        client_strategy = ClientStrategyFactory.create_strategy(self.tracer)
        
        # Get request mode and determine if we need to upload git metadata
        self._requests_mode = client_strategy.get_request_mode()
        self._should_upload_git_metadata = client_strategy.should_upload_git_metadata()
        requests_mode_str = client_strategy.get_mode_name()
        
        # Create API client using strategy
        self._api_client = client_strategy.create_api_client(
            self._itr_skipping_level,
            self._git_data,
            self._configurations,
            self._service,
            self.tracer,
            env_config
        )
        
        # Initialize git client if needed
        if self._should_upload_git_metadata:
            self._git_client = CIVisibilityGitClient(
                api_key=env_config.api_key or "", 
                requests_mode=self._requests_mode, 
                tracer=self.tracer
            )
            self._git_client.upload_git_metadata(cwd=_get_git_repo())

        # Initialize settings and coverage
        self._api_settings = self._check_enabled_features()
        self._feature_manager = FeatureManager(self._api_settings)
        self._collect_coverage_enabled = self._should_collect_coverage(self._api_settings.coverage_enabled)
        
        # Configure writer
        self._configure_writer(coverage_enabled=self._collect_coverage_enabled, url=self.tracer._agent_url)

        # Log configuration
        self._log_configuration(requests_mode_str, env_config)
        
        # Initialize codeowners
        self._initialize_codeowners()

    @classmethod
    def is_itr_enabled(cls) -> bool:
        """
        Check whether Intelligent Test Runner is enabled
        :return: True if ITR is enabled, False otherwise
        """
        instance = cls._instance
        if instance is None or instance.feature_manager is None:
            return False
        return instance.feature_manager.is_itr_enabled()

    @classmethod
    def test_skipping_enabled(cls) -> bool:
        """
        Check whether test skipping is enabled
        :return: True if test skipping is enabled, False otherwise
        """
        instance = cls._instance
        if instance is None or instance.feature_manager is None:
            return False
        return instance.feature_manager.is_test_skipping_enabled()

    @classmethod
    def is_efd_enabled(cls) -> bool:
        """
        Check whether Early Flake Detection is enabled
        :return: True if EFD is enabled, False otherwise
        """
        instance = cls._instance
        if instance is None or instance.feature_manager is None:
            return False
        return instance.feature_manager.is_efd_enabled()

    @classmethod
    def is_atr_enabled(cls) -> bool:
        if cls._instance is None:
            return False
        return cls._instance._feature_manager.atr_enabled

    @classmethod
    def is_test_management_enabled(cls) -> bool:
        if cls._instance is None:
            return False
        return cls._instance._feature_manager.test_management_enabled

    @classmethod
    def should_collect_coverage(cls) -> bool:
        if cls._instance is None:
            return False
        return cls._instance._feature_manager.should_collect_coverage

    @classmethod
    def get_efd_api_settings(cls) -> Optional[EarlyFlakeDetectionSettings]:
        if not cls.enabled or cls._instance is None:
            log.warning("CI Visibility is not enabled")
            raise CIVisibilityError("CI Visibility is not enabled")
        return cls._instance._feature_manager.get_efd_settings()

    @classmethod
    def get_atr_api_settings(cls) -> Optional[AutoTestRetriesSettings]:
        if not cls.enabled or cls._instance is None:
            log.warning("CI Visibility is not enabled")
            raise CIVisibilityError("CI Visibility is not enabled")
        return cls._instance._feature_manager.get_atr_settings()

    @classmethod
    def get_test_management_api_settings(cls) -> Optional[TestManagementSettings]:
        if not cls.enabled or cls._instance is None:
            log.warning("CI Visibility is not enabled")
            raise CIVisibilityError("CI Visibility is not enabled")
        return cls._instance._feature_manager.get_test_management_settings()

    @classmethod
    def enable(
        cls,
        service: Optional[str] = None,
        config: Optional[Config] = None,
        tracer: Optional[Tracer] = None,
        api_client_factory: Optional[ApiClientFactory] = None,
        **tags,
    ) -> Optional["CIVisibility"]:
        if cls.has_instance():
            # CI Visibility has already been enabled
            # noinspection PyProtectedMember
            if service is not None and service != cls._instance._service:
                log.warning(
                    "%s was already enabled with the service name %s, cannot change to service %s",
                    cls.__name__,
                    cls._instance._service,
                    service,
                )
            return cls._instance

        log.debug("Enabling %s", cls.__name__)
        if ddconfig._ci_visibility_agentless_enabled:
            api_key = os.getenv("_CI_DD_API_KEY", os.getenv("DD_API_KEY"))
            if not api_key:
                log.critical(
                    "%s disabled: environment variable DD_CIVISIBILITY_AGENTLESS_ENABLED is true but"
                    " DD_API_KEY is not set",
                    cls.__name__,
                )
                return None

        try:
            # prevent swallowing of exceptions during _init
            instance = CIVisibility(
                service=service,
                tracer=tracer,
                config=config,
                api_client_factory=api_client_factory,
                **tags,
            )
            cls._instance = instance
            
            # Initialize feature flags
            try:
                api_settings = instance._check_enabled_features()
                instance.feature_manager = FeatureManager(api_settings)
            except CIVisibilityAuthenticationException:
                log.warning("CI Visibility authentication failed")
                return None
            except Exception:
                log.warning("Error checking API settings", exc_info=True)
                instance.feature_manager = FeatureManager(TestVisibilityAPISettings())
            
            # Configure coverage collection
            coverage_enabled = instance.feature_manager.is_coverage_collection_enabled()
            instance._configure_writer(coverage_enabled=coverage_enabled)

            # Log configuration information
            requests_mode_str = "AGENT_HTTP_API"
            if instance._requests_mode == REQUESTS_MODE.AGENTLESS_EVENTS:
                requests_mode_str = "AGENTLESS_EVENTS"
            elif instance._requests_mode == REQUESTS_MODE.EVP_PROXY_EVENTS:
                requests_mode_str = "EVP_PROXY_EVENTS"
            
            env_config = EnvConfig.from_environment()
            instance._log_configuration(requests_mode_str, env_config)

            # Initialize CODEOWNERS if available
            instance._initialize_codeowners()
            
            # Get tests to skip if ITR skipping is enabled
            if instance.feature_manager.is_test_skipping_enabled():
                instance._fetch_tests_to_skip()
                
            # Start the service
            instance.start()
            atexit.register(cls.disable)
            
            log.debug("%s enabled", cls.__name__)
            instance.feature_manager.log_settings()
            
            return instance
        except Exception:
            cls._instance = None
            log.warning("Failed to initialize CI Visibility", exc_info=True)
            return None

    @classmethod
    def disable(cls) -> None:
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

    def start(self) -> None:
        """Start the CI Visibility service"""
        import threading
        
        if self._is_session_started:
            log.debug("CIVisibility service already started")
            return
            
        log.debug("Starting CIVisibility service")
        try:
            # Start runtime worker for sending metrics
            self._runtime_worker = RuntimeWorker(
                self.tracer, interval=ddconfig.ci_visibility.service_runtime_metrics_interval
            )
            threading.Thread(target=self._runtime_worker.periodic, name="ddtrace.ci-runtime-metrics").start()
            
            # Enable telemetry
            try:
                enable_telemetry()
            except Exception:
                log.warning("Failed to enable telemetry", exc_info=True)
            
            # Enable git metadata collection if needed
            if self._should_upload_git_metadata and self._git_client is not None and not is_pytest_xdist_worker():
                self._git_client.start()
                
            self._is_session_started = True
        except Exception:
            log.warning("Failed to start CI Visibility service", exc_info=True)

    def _start_service(self) -> None:
        tracer_filters = self.tracer._user_trace_processors
        if not any(isinstance(tracer_filter, TraceCiVisibilityFilter) for tracer_filter in tracer_filters):
            tracer_filters += [TraceCiVisibilityFilter(self._tags, self._service)]  # type: ignore[arg-type]
            self.tracer._configure(trace_processors=tracer_filters)

        if self.test_skipping_enabled():
            self._fetch_tests_to_skip()
            if self._itr_data is None:
                log.warning("Failed to fetch skippable items, no tests will be skipped.")
                return
            log.info("Intelligent Test Runner skipping level: %s", "suite" if self._suite_skipping_mode else "test")
            log.info("Skippable items fetched: %s", len(self._itr_data.skippable_items))
            log.info("ITR correlation ID: %s", self._itr_data.correlation_id)

        if CIVisibility.is_efd_enabled():
            unique_test_ids = self._fetch_unique_tests()
            if unique_test_ids is None:
                log.warning("Failed to fetch unique tests for Early Flake Detection")
            else:
                self._unique_test_ids = unique_test_ids
                log.info("Unique tests fetched for Early Flake Detection: %s", len(self._unique_test_ids))
        else:
            if (
                self._api_settings.early_flake_detection.enabled
                and not ddconfig._test_visibility_early_flake_detection_enabled
            ):
                log.warning(
                    "Early Flake Detection is enabled by API but disabled by "
                    "DD_TEST_VISIBILITY_EARLY_FLAKE_DETECTION_ENABLED environment variable"
                )

        if self._api_settings.flaky_test_retries_enabled and not asbool(
            os.environ.get("DD_CIVISIBILITY_FLAKY_RETRY_ENABLED", True)
        ):
            log.warning(
                "Auto Test Retries is enabled by API but disabled by "
                "DD_CIVISIBILITY_FLAKY_RETRY_ENABLED environment variable"
            )

        if self._api_settings.test_management.enabled:
            test_properties = self._fetch_test_management_tests()
            if test_properties is None:
                log.warning("Failed to fetch quarantined tests from Test Management")
            else:
                self._test_properties = test_properties

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
    def get_efd_api_settings(cls) -> Optional[EarlyFlakeDetectionSettings]:
        if not cls.enabled or cls._instance is None:
            log.warning("CI Visibility is not enabled")
            raise CIVisibilityError("CI Visibility is not enabled")
        return cls._instance._feature_manager.get_efd_settings()

    @classmethod
    def get_atr_api_settings(cls) -> Optional[AutoTestRetriesSettings]:
        if not cls.enabled or cls._instance is None:
            log.warning("CI Visibility is not enabled")
            raise CIVisibilityError("CI Visibility is not enabled")
        return cls._instance._feature_manager.get_atr_settings()

    @classmethod
    def get_test_management_api_settings(cls) -> Optional[TestManagementSettings]:
        if not cls.enabled or cls._instance is None:
            log.warning("CI Visibility is not enabled")
            raise CIVisibilityError("CI Visibility is not enabled")
        return cls._instance._feature_manager.get_test_management_settings()

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
    def is_item_itr_skippable(cls, item_id: TestVisibilityItemId) -> bool:
        if not cls.enabled:
            error_msg = "CI Visibility is not enabled"
            log.warning(error_msg)
            raise CIVisibilityError(error_msg)
        instance = cls.get_instance()
        if instance is None or instance._itr_data is None:
            return False

        if isinstance(item_id, TestSuiteId) and not instance._suite_skipping_mode:
            log.debug("Skipping mode is suite, but item is not a suite: %s", item_id)
            return False

        if isinstance(item_id, TestId) and instance._suite_skipping_mode:
            log.debug("Skipping mode is test, but item is not a test: %s", item_id)
            return False
        return item_id in instance._itr_data.skippable_items

    @classmethod
    def is_unknown_ci(cls) -> bool:
        instance = cls.get_instance()
        if instance is None:
            return False

        return instance._tags.get(ci.PROVIDER_NAME) is None

    @classmethod
    def ci_provider_name_for_telemetry(cls) -> str:
        instance = cls.get_instance()
        if instance is None:
            return UNSUPPORTED_PROVIDER
        return TELEMETRY_BY_PROVIDER_NAME.get(instance._tags.get(ci.PROVIDER_NAME, UNSUPPORTED), UNSUPPORTED_PROVIDER)

    @classmethod
    def is_auto_injected(cls) -> bool:
        instance = cls.get_instance()
        if instance is None:
            return False
        return instance._is_auto_injected

    def _get_ci_visibility_event_client(self) -> Optional[CIVisibilityEventClient]:
        writer = self.tracer._writer
        if isinstance(writer, CIVisibilityWriter):
            for client in writer._clients:
                if isinstance(client, CIVisibilityEventClient):
                    return client

        return None

    @classmethod
    def set_test_session_name(cls, test_command: str) -> None:
        instance = cls.get_instance()
        client = instance._get_ci_visibility_event_client()
        if not client:
            log.debug("Not setting test session name because no CIVisibilityEventClient is active")
            return

        if ddconfig._test_session_name:
            test_session_name = ddconfig._test_session_name
        else:
            job_name = instance._tags.get(ci.JOB_NAME)
            test_session_name = f"{job_name}-{test_command}" if job_name else test_command

        log.debug("Setting test session name: %s", test_session_name)
        client.set_test_session_name(test_session_name)

    @classmethod
    def set_library_capabilities(cls, capabilities: LibraryCapabilities) -> None:
        instance = cls.get_instance()
        client = instance._get_ci_visibility_event_client()
        if not client:
            log.debug("Not setting library capabilities because no CIVisibilityEventClient is active")
            return
        client.set_metadata("test", capabilities.tags())

    @classmethod
    def is_unique_test(cls, test_id: Union[TestId, InternalTestId]) -> bool:
        instance = cls.get_instance()
        if instance is None:
            return False

        # The assumption that we were not able to fetch unique tests properly if the length is 0 is acceptable
        # because the current EFD usage would cause the session to be faulty even if the query was successful but
        # not unique tests exist. In this case, we assume all tests are unique.
        if len(instance._unique_test_ids) == 0:
            return True

        return test_id in instance._unique_test_ids

    @classmethod
    def get_test_properties(cls, test_id: Union[TestId, InternalTestId]) -> Optional[TestProperties]:
        instance = cls.get_instance()
        if instance is None:
            return None

        return instance._test_properties.get(test_id)

    @property
    def test_skipping_mode(self) -> Optional[str]:
        if self.feature_manager is None:
            return None
            
        return SUITE if self._suite_skipping_mode else TEST
    
    @property
    def module_skipping_mode(self) -> Optional[str]:
        # This isn't actually implemented yet, but might be in the future
        # In this case, we'd skip entire modules
        return "module" if self._suite_skipping_mode else None


def _requires_civisibility_enabled(func: Callable) -> Callable:
    def wrapper(*args, **kwargs) -> Any:
        if not CIVisibility.enabled:
            log.warning("CI Visibility is not enabled")
            raise CIVisibilityError("CI Visibility is not enabled")
        return func(*args, **kwargs)

    return wrapper


@_requires_civisibility_enabled
def _on_discover_session(discover_args: TestSession.DiscoverArgs) -> None:
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

    # Prevent high cardinality of test framework telemetry tag by matching with known frameworks
    test_framework_telemetry_name = _get_test_framework_telemetry_name(discover_args.test_framework)

    efd_api_settings = CIVisibility.get_efd_api_settings()
    if efd_api_settings is None or not CIVisibility.is_efd_enabled():
        efd_api_settings = EarlyFlakeDetectionSettings()

    atr_api_settings = CIVisibility.get_atr_api_settings()
    if atr_api_settings is None or not CIVisibility.is_atr_enabled():
        atr_api_settings = AutoTestRetriesSettings()

    test_management_api_settings = CIVisibility.get_test_management_api_settings()
    if test_management_api_settings is None or not CIVisibility.is_test_management_enabled():
        test_management_api_settings = TestManagementSettings()

    session_settings = TestVisibilitySessionSettings(
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
        itr_test_skipping_level=instance._itr_skipping_level,
        itr_correlation_id=instance._itr_meta.get(ITR_CORRELATION_ID_TAG_NAME, ""),
        coverage_enabled=CIVisibility.should_collect_coverage(),
        efd_settings=efd_api_settings,
        atr_settings=atr_api_settings,
        test_management_settings=test_management_api_settings,
        ci_provider_name=CIVisibility.ci_provider_name_for_telemetry(),
        is_auto_injected=CIVisibility.is_auto_injected(),
    )

    session = TestVisibilitySession(
        session_settings,
    )

    CIVisibility.add_session(session)
    CIVisibility.set_test_session_name(test_command=discover_args.test_command)


@_requires_civisibility_enabled
def _on_start_session() -> None:
    log.debug("Handling start session")
    session = CIVisibility.get_session()
    session.start()


@_requires_civisibility_enabled
def _on_finish_session(finish_args: TestSession.FinishArgs) -> None:
    log.debug("Handling finish session")
    session = CIVisibility.get_session()
    session.finish(finish_args.force_finish_children, finish_args.override_status)


@_requires_civisibility_enabled
def _on_session_is_test_skipping_enabled() -> bool:
    log.debug("Handling is test skipping enabled")
    return CIVisibility.test_skipping_enabled()


@_requires_civisibility_enabled
def _on_session_get_workspace_path() -> Optional[Path]:
    log.debug("Handling finish for session id %s")
    path_str = CIVisibility.get_workspace_path()
    return Path(path_str) if path_str is not None else None


@_requires_civisibility_enabled
def _on_session_should_collect_coverage() -> bool:
    log.debug("Handling should collect coverage")
    return CIVisibility.should_collect_coverage()


@_requires_civisibility_enabled
def _on_session_get_codeowners() -> Optional[Codeowners]:
    log.debug("Getting codeowners")
    return CIVisibility.get_codeowners()


@_requires_civisibility_enabled
def _on_session_get_tracer() -> Optional[Tracer]:
    log.debug("Getting tracer")
    return CIVisibility.get_tracer()


@_requires_civisibility_enabled
def _on_session_is_atr_enabled() -> bool:
    log.debug("Getting Auto Test Retries enabled")
    return CIVisibility.is_atr_enabled()


@_requires_civisibility_enabled
def _on_session_is_efd_enabled() -> bool:
    log.debug("Getting Early Flake Detection enabled")
    return CIVisibility.is_efd_enabled()


@_requires_civisibility_enabled
def _on_session_set_covered_lines_pct(coverage_pct) -> None:
    log.debug("Setting coverage percentage for session to %s", coverage_pct)
    CIVisibility.get_session().set_covered_lines_pct(coverage_pct)


@_requires_civisibility_enabled
def _on_session_set_library_capabilities(capabilities: LibraryCapabilities) -> None:
    log.debug("Setting library capabilities")
    CIVisibility.set_library_capabilities(capabilities)


@_requires_civisibility_enabled
def _on_session_get_path_codeowners(path: Path) -> Optional[List[str]]:
    log.debug("Getting codeowners for path %s", path)
    codeowners = CIVisibility.get_codeowners()
    if codeowners is None:
        return None
    return codeowners.of(str(path))


def _register_session_handlers() -> None:
    log.debug("Registering session handlers")
    core.on("test_visibility.session.discover", _on_discover_session)
    core.on("test_visibility.session.start", _on_start_session)
    core.on("test_visibility.session.finish", _on_finish_session)
    core.on("test_visibility.session.get_codeowners", _on_session_get_codeowners, "codeowners")
    core.on("test_visibility.session.get_tracer", _on_session_get_tracer, "tracer")
    core.on("test_visibility.session.get_path_codeowners", _on_session_get_path_codeowners, "path_codeowners")
    core.on("test_visibility.session.get_workspace_path", _on_session_get_workspace_path, "workspace_path")
    core.on("test_visibility.session.is_atr_enabled", _on_session_is_atr_enabled, "is_atr_enabled")
    core.on("test_visibility.session.is_efd_enabled", _on_session_is_efd_enabled, "is_efd_enabled")
    core.on(
        "test_visibility.session.should_collect_coverage",
        _on_session_should_collect_coverage,
        "should_collect_coverage",
    )
    core.on(
        "test_visibility.session.is_test_skipping_enabled",
        _on_session_is_test_skipping_enabled,
        "is_test_skipping_enabled",
    )
    core.on("test_visibility.session.set_covered_lines_pct", _on_session_set_covered_lines_pct)
    core.on("test_visibility.session.set_library_capabilities", _on_session_set_library_capabilities)


@_requires_civisibility_enabled
def _on_discover_module(discover_args: TestModule.DiscoverArgs) -> None:
    log.debug("Handling discovery for module %s", discover_args.module_id)
    session = CIVisibility.get_session()

    session.add_child(
        discover_args.module_id,
        TestVisibilityModule(
            discover_args.module_id.name,
            CIVisibility.get_session_settings(),
            discover_args.module_path,
        ),
    )


@_requires_civisibility_enabled
def _on_start_module(module_id: TestModuleId) -> None:
    log.debug("Handling start for module id %s", module_id)
    CIVisibility.get_module_by_id(module_id).start()


@_requires_civisibility_enabled
def _on_finish_module(finish_args: TestModule.FinishArgs) -> None:
    log.debug("Handling finish for module id %s", finish_args.module_id)
    CIVisibility.get_module_by_id(finish_args.module_id).finish()


def _register_module_handlers() -> None:
    log.debug("Registering module handlers")
    core.on("test_visibility.module.discover", _on_discover_module)
    core.on("test_visibility.module.start", _on_start_module)
    core.on("test_visibility.module.finish", _on_finish_module)


@_requires_civisibility_enabled
def _on_discover_suite(discover_args: TestSuite.DiscoverArgs) -> None:
    log.debug("Handling discovery for suite args %s", discover_args)
    module = CIVisibility.get_module_by_id(discover_args.suite_id.parent_id)

    module.add_child(
        discover_args.suite_id,
        TestVisibilitySuite(
            discover_args.suite_id.name,
            CIVisibility.get_session_settings(),
            discover_args.codeowners,
            discover_args.source_file_info,
        ),
    )


@_requires_civisibility_enabled
def _on_start_suite(suite_id: TestSuiteId) -> None:
    log.debug("Handling start for suite id %s", suite_id)
    CIVisibility.get_suite_by_id(suite_id).start()


@_requires_civisibility_enabled
def _on_finish_suite(finish_args: TestSuite.FinishArgs) -> None:
    log.debug("Handling finish for suite id %s", finish_args.suite_id)
    CIVisibility.get_suite_by_id(finish_args.suite_id).finish(
        finish_args.force_finish_children, finish_args.override_status
    )


def _register_suite_handlers() -> None:
    log.debug("Registering suite handlers")
    core.on("test_visibility.suite.discover", _on_discover_suite)
    core.on("test_visibility.suite.start", _on_start_suite)
    core.on("test_visibility.suite.finish", _on_finish_suite)


@_requires_civisibility_enabled
def _on_discover_test(discover_args: Test.DiscoverArgs) -> None:
    log.debug("Handling discovery for test %s", discover_args.test_id)
    suite = CIVisibility.get_suite_by_id(discover_args.test_id.parent_id)

    # New tests are currently only considered for EFD:
    # - if known tests were fetched properly (enforced by is_unique_test)
    # - if they have no parameters
    if CIVisibility.is_efd_enabled() and discover_args.test_id.parameters is None:
        is_new = not CIVisibility.is_unique_test(discover_args.test_id)
    else:
        is_new = False

    test_properties = None
    if CIVisibility.is_test_management_enabled():
        test_properties = CIVisibility.get_test_properties(discover_args.test_id)

    if not test_properties:
        test_properties = TestProperties()

    suite.add_child(
        discover_args.test_id,
        TestVisibilityTest(
            discover_args.test_id.name,
            CIVisibility.get_session_settings(),
            parameters=discover_args.test_id.parameters,
            codeowners=discover_args.codeowners,
            source_file_info=discover_args.source_file_info,
            resource=discover_args.resource,
            is_new=is_new,
            is_quarantined=test_properties.quarantined,
            is_disabled=test_properties.disabled,
            is_attempt_to_fix=test_properties.attempt_to_fix,
        ),
    )


@_requires_civisibility_enabled
def _on_is_new_test(test_id: Union[TestId, InternalTestId]) -> bool:
    log.debug("Handling is new test for test %s", test_id)
    return CIVisibility.get_test_by_id(test_id).is_new()


@_requires_civisibility_enabled
def _on_is_quarantined_test(test_id: Union[TestId, InternalTestId]) -> bool:
    log.debug("Handling is quarantined test for test %s", test_id)
    return CIVisibility.get_test_by_id(test_id).is_quarantined()


@_requires_civisibility_enabled
def _on_is_disabled_test(test_id: Union[TestId, InternalTestId]) -> bool:
    log.debug("Handling is disabled test for test %s", test_id)
    return CIVisibility.get_test_by_id(test_id).is_disabled()


@_requires_civisibility_enabled
def _on_is_attempt_to_fix(test_id: Union[TestId, InternalTestId]) -> bool:
    log.debug("Handling is attempt to fix for test %s", test_id)
    return CIVisibility.get_test_by_id(test_id).is_attempt_to_fix()


@_requires_civisibility_enabled
def _on_start_test(test_id: TestId) -> None:
    log.debug("Handling start for test id %s", test_id)
    CIVisibility.get_test_by_id(test_id).start()


@_requires_civisibility_enabled
def _on_finish_test(finish_args: Test.FinishArgs) -> None:
    log.debug("Handling finish for test id %s, with status %s", finish_args.test_id, finish_args.status)
    CIVisibility.get_test_by_id(finish_args.test_id).finish_test(
        finish_args.status, finish_args.skip_reason, finish_args.exc_info
    )


@_requires_civisibility_enabled
def _on_set_test_parameters(item_id: TestId, parameters: str) -> None:
    log.debug("Handling set parameters for test id %s, parameters %s", item_id, parameters)
    CIVisibility.get_test_by_id(item_id).set_parameters(parameters)


@_requires_civisibility_enabled
def _on_set_benchmark_data(set_benchmark_data_args: BenchmarkTestMixin.SetBenchmarkDataArgs) -> None:
    item_id = set_benchmark_data_args.test_id
    data = set_benchmark_data_args.benchmark_data
    is_benchmark = set_benchmark_data_args.is_benchmark
    log.debug("Handling set benchmark data for test id %s, data %s, is_benchmark %s", item_id, data, is_benchmark)
    CIVisibility.get_test_by_id(item_id).set_benchmark_data(data, is_benchmark)


@_requires_civisibility_enabled
def _on_test_overwrite_attributes(overwrite_attribute_args: InternalTest.OverwriteAttributesArgs) -> None:
    item_id = overwrite_attribute_args.test_id
    name = overwrite_attribute_args.name
    suite_name = overwrite_attribute_args.suite_name
    parameters = overwrite_attribute_args.parameters
    codeowners = overwrite_attribute_args.codeowners

    log.debug("Handling overwrite attributes: %s", overwrite_attribute_args)
    CIVisibility.get_test_by_id(item_id).overwrite_attributes(name, suite_name, parameters, codeowners)


def _register_test_handlers():
    log.debug("Registering test handlers")
    core.on("test_visibility.test.discover", _on_discover_test)
    core.on("test_visibility.test.is_new", _on_is_new_test, "is_new")
    core.on("test_visibility.test.is_quarantined", _on_is_quarantined_test, "is_quarantined")
    core.on("test_visibility.test.is_disabled", _on_is_disabled_test, "is_disabled")
    core.on("test_visibility.test.is_attempt_to_fix", _on_is_attempt_to_fix, "is_attempt_to_fix")
    core.on("test_visibility.test.start", _on_start_test)
    core.on("test_visibility.test.finish", _on_finish_test)
    core.on("test_visibility.test.set_parameters", _on_set_test_parameters)
    core.on("test_visibility.test.set_benchmark_data", _on_set_benchmark_data)
    core.on("test_visibility.test.overwrite_attributes", _on_test_overwrite_attributes)


@_requires_civisibility_enabled
def _on_item_get_span(item_id: TestVisibilityItemId) -> Optional[Span]:
    log.debug("Handing get_span for item %s", item_id)
    item = CIVisibility.get_item_by_id(item_id)
    return item.get_span()


@_requires_civisibility_enabled
def _on_item_is_finished(item_id: TestVisibilityItemId) -> bool:
    log.debug("Handling is finished for item %s", item_id)
    return CIVisibility.get_item_by_id(item_id).is_finished()


@_requires_civisibility_enabled
def _on_item_stash_set(item_id: TestVisibilityItemId, key: str, value: object) -> None:
    log.debug("Handling stash set for item %s, key %s, value %s", item_id, key, value)
    CIVisibility.get_item_by_id(item_id).stash_set(key, value)


@_requires_civisibility_enabled
def _on_item_stash_get(item_id: TestVisibilityItemId, key: str) -> Optional[object]:
    log.debug("Handling stash get for item %s, key %s", item_id, key)
    return CIVisibility.get_item_by_id(item_id).stash_get(key)


@_requires_civisibility_enabled
def _on_item_stash_delete(item_id: TestVisibilityItemId, key: str) -> None:
    log.debug("Handling stash delete for item %s, key %s", item_id, key)
    CIVisibility.get_item_by_id(item_id).stash_delete(key)


def _register_item_handlers() -> None:
    log.debug("Registering item handlers")
    core.on("test_visibility.item.get_span", _on_item_get_span, "span")
    core.on("test_visibility.item.is_finished", _on_item_is_finished, "is_finished")
    core.on("test_visibility.item.stash_set", _on_item_stash_set)
    core.on("test_visibility.item.stash_get", _on_item_stash_get, "stash_value")
    core.on("test_visibility.item.stash_delete", _on_item_stash_delete)


@_requires_civisibility_enabled
def _on_get_coverage_data(item_id: Union[TestSuiteId, TestId]) -> Optional[Dict[Path, CoverageLines]]:
    log.debug("Handling get coverage data for item %s", item_id)
    return CIVisibility.get_item_by_id(item_id).get_coverage_data()


@_requires_civisibility_enabled
def _on_add_coverage_data(add_coverage_args: ITRMixin.AddCoverageArgs) -> None:
    """Adds coverage data to an item, merging with existing coverage data if necessary"""
    item_id = add_coverage_args.item_id
    coverage_data = add_coverage_args.coverage_data

    log.debug("Handling add coverage data for item id %s", item_id)

    if not isinstance(item_id, (TestSuiteId, TestId)):
        log.warning("Coverage data can only be added to suites and tests, not %s", type(item_id))
        return

    CIVisibility.get_item_by_id(item_id).add_coverage_data(coverage_data)


def _register_coverage_handlers() -> None:
    log.debug("Registering coverage handlers")
    core.on("test_visibility.item.get_coverage_data", _on_get_coverage_data, "coverage_data")
    core.on("test_visibility.item.add_coverage_data", _on_add_coverage_data)


@_requires_civisibility_enabled
def _on_get_tag(get_tag_args: TestBase.GetTagArgs) -> Any:
    item_id = get_tag_args.item_id
    key = get_tag_args.name
    log.debug("Handling get tag for item id %s, key %s", item_id, key)
    return CIVisibility.get_item_by_id(item_id).get_tag(key)


@_requires_civisibility_enabled
def _on_set_tag(set_tag_args: TestBase.SetTagArgs) -> None:
    item_id = set_tag_args.item_id
    key = set_tag_args.name
    value = set_tag_args.value
    log.debug("Handling set tag for item id %s, key %s, value %s", item_id, key, value)
    CIVisibility.get_item_by_id(item_id).set_tag(key, value)


@_requires_civisibility_enabled
def _on_set_tags(set_tags_args: TestBase.SetTagsArgs) -> None:
    item_id = set_tags_args.item_id
    tags = set_tags_args.tags
    log.debug("Handling set tags for item id %s, tags %s", item_id, tags)
    CIVisibility.get_item_by_id(item_id).set_tags(tags)


@_requires_civisibility_enabled
def _on_delete_tag(delete_tag_args: TestBase.DeleteTagArgs) -> None:
    item_id = delete_tag_args.item_id
    key = delete_tag_args.name
    log.debug("Handling delete tag for item id %s, key %s", item_id, key)
    CIVisibility.get_item_by_id(item_id).delete_tag(key)


@_requires_civisibility_enabled
def _on_delete_tags(delete_tags_args: TestBase.DeleteTagsArgs) -> None:
    item_id = delete_tags_args.item_id
    keys = delete_tags_args.names
    log.debug("Handling delete tags for item id %s, keys %s", item_id, keys)
    CIVisibility.get_item_by_id(item_id).delete_tags(keys)


def _register_tag_handlers() -> None:
    log.debug("Registering tag handlers")
    core.on("test_visibility.item.get_tag", _on_get_tag, "tag_value")
    core.on("test_visibility.item.set_tag", _on_set_tag)
    core.on("test_visibility.item.set_tags", _on_set_tags)
    core.on("test_visibility.item.delete_tag", _on_delete_tag)
    core.on("test_visibility.item.delete_tags", _on_delete_tags)


@_requires_civisibility_enabled
def _on_itr_finish_item_skipped(item_id: Union[TestSuiteId, TestId]) -> None:
    log.debug("Handling finish ITR skipped for item id %s", item_id)
    if not isinstance(item_id, (TestSuiteId, TestId)):
        log.warning("Only suites or tests can be skipped, not %s", type(item_id))
        return
    CIVisibility.get_item_by_id(item_id).finish_itr_skipped()


@_requires_civisibility_enabled
def _on_itr_mark_unskippable(item_id: Union[TestSuiteId, TestId]) -> None:
    log.debug("Handling marking %s unskippable", item_id)
    CIVisibility.get_item_by_id(item_id).mark_itr_unskippable()


@_requires_civisibility_enabled
def _on_itr_mark_forced_run(item_id: Union[TestSuiteId, TestId]) -> None:
    log.debug("Handling marking %s as forced run", item_id)
    CIVisibility.get_item_by_id(item_id).mark_itr_forced_run()


@_requires_civisibility_enabled
def _on_itr_was_forced_run(item_id: TestVisibilityItemId) -> bool:
    log.debug("Handling marking %s as forced run", item_id)
    return CIVisibility.get_item_by_id(item_id).was_itr_forced_run()


@_requires_civisibility_enabled
def _on_itr_is_item_skippable(item_id: Union[TestSuiteId, TestId]) -> bool:
    """Skippable items are fetched as part CIVisibility.enable(), so they are assumed to be available."""
    log.debug("Handling is item skippable for item id %s", item_id)

    if not isinstance(item_id, (TestSuiteId, TestId)):
        log.warning("Only suites or tests can be skippable, not %s", type(item_id))
        return False

    if not CIVisibility.test_skipping_enabled():
        log.debug("Test skipping is not enabled")
        return False

    return CIVisibility.is_item_itr_skippable(item_id)


@_requires_civisibility_enabled
def _on_itr_is_item_unskippable(item_id: Union[TestSuiteId, TestId]) -> bool:
    log.debug("Handling is item unskippable for %s", item_id)
    if not isinstance(item_id, (TestSuiteId, TestId)):
        raise CIVisibilityError("Only suites or tests can be unskippable")
    return CIVisibility.get_item_by_id(item_id).is_itr_unskippable()


@_requires_civisibility_enabled
def _on_itr_was_item_skipped(item_id: Union[TestSuiteId, TestId]) -> bool:
    log.debug("Handling was item skipped for %s", item_id)
    return CIVisibility.get_item_by_id(item_id).is_itr_skipped()


def _register_itr_handlers() -> None:
    log.debug("Registering ITR-related handlers")
    core.on("test_visibility.itr.finish_skipped_by_itr", _on_itr_finish_item_skipped)
    core.on("test_visibility.itr.is_item_skippable", _on_itr_is_item_skippable, "is_item_skippable")
    core.on("test_visibility.itr.was_item_skipped", _on_itr_was_item_skipped, "was_item_skipped")

    core.on("test_visibility.itr.is_item_unskippable", _on_itr_is_item_unskippable, "is_item_unskippable")
    core.on("test_visibility.itr.mark_forced_run", _on_itr_mark_forced_run)
    core.on("test_visibility.itr.mark_unskippable", _on_itr_mark_unskippable)
    core.on("test_visibility.itr.was_forced_run", _on_itr_was_forced_run, "was_forced_run")


#
# EFD handlers
#


@_requires_civisibility_enabled
def _on_efd_is_enabled() -> bool:
    return CIVisibility.get_session().efd_is_enabled()


@_requires_civisibility_enabled
def _on_efd_session_is_faulty() -> bool:
    return CIVisibility.get_session().efd_is_faulty_session()


@_requires_civisibility_enabled
def _on_efd_session_has_efd_failed_tests() -> bool:
    return CIVisibility.get_session().efd_has_failed_tests()


@_requires_civisibility_enabled
def _on_efd_should_retry_test(test_id: InternalTestId) -> bool:
    return CIVisibility.get_test_by_id(test_id).efd_should_retry()


@_requires_civisibility_enabled
def _on_efd_add_retry(test_id: InternalTestId, retry_number: int) -> Optional[int]:
    return CIVisibility.get_test_by_id(test_id).efd_add_retry(retry_number)


@_requires_civisibility_enabled
def _on_efd_start_retry(test_id: InternalTestId, retry_number: int) -> None:
    CIVisibility.get_test_by_id(test_id).efd_start_retry(retry_number)


@_requires_civisibility_enabled
def _on_efd_finish_retry(efd_finish_args: EFDTestMixin.EFDRetryFinishArgs) -> None:
    CIVisibility.get_test_by_id(efd_finish_args.test_id).efd_finish_retry(
        efd_finish_args.retry_number, efd_finish_args.status, efd_finish_args.exc_info
    )


@_requires_civisibility_enabled
def _on_efd_get_final_status(test_id: InternalTestId) -> EFDTestStatus:
    return CIVisibility.get_test_by_id(test_id).efd_get_final_status()


def _register_efd_handlers() -> None:
    log.debug("Registering EFD handlers")
    core.on("test_visibility.efd.is_enabled", _on_efd_is_enabled, "is_enabled")
    core.on("test_visibility.efd.session_is_faulty", _on_efd_session_is_faulty, "is_faulty_session")
    core.on("test_visibility.efd.session_has_failed_tests", _on_efd_session_has_efd_failed_tests, "has_failed_tests")
    core.on("test_visibility.efd.should_retry_test", _on_efd_should_retry_test, "should_retry_test")
    core.on("test_visibility.efd.add_retry", _on_efd_add_retry, "retry_number")
    core.on("test_visibility.efd.start_retry", _on_efd_start_retry)
    core.on("test_visibility.efd.finish_retry", _on_efd_finish_retry)
    core.on("test_visibility.efd.get_final_status", _on_efd_get_final_status, "efd_final_status")


@_requires_civisibility_enabled
def _on_atr_is_enabled() -> bool:
    return CIVisibility.is_atr_enabled()


@_requires_civisibility_enabled
def _on_atr_session_has_failed_tests() -> bool:
    return CIVisibility.get_session().atr_has_failed_tests()


@_requires_civisibility_enabled
def _on_atr_should_retry_test(item_id: InternalTestId) -> bool:
    return CIVisibility.get_test_by_id(item_id).atr_should_retry()


@_requires_civisibility_enabled
def _on_atr_add_retry(item_id: InternalTestId, retry_number: int) -> Optional[int]:
    return CIVisibility.get_test_by_id(item_id).atr_add_retry(retry_number)


@_requires_civisibility_enabled
def _on_atr_start_retry(test_id: InternalTestId, retry_number: int) -> None:
    CIVisibility.get_test_by_id(test_id).atr_start_retry(retry_number)


@_requires_civisibility_enabled
def _on_atr_finish_retry(atr_finish_args: ATRTestMixin.ATRRetryFinishArgs) -> None:
    CIVisibility.get_test_by_id(atr_finish_args.test_id).atr_finish_retry(
        atr_finish_args.retry_number, atr_finish_args.status, atr_finish_args.exc_info
    )


@_requires_civisibility_enabled
def _on_atr_get_final_status(test_id: InternalTestId) -> TestStatus:
    return CIVisibility.get_test_by_id(test_id).atr_get_final_status()


def _register_atr_handlers() -> None:
    log.debug("Registering ATR handlers")
    core.on("test_visibility.atr.is_enabled", _on_atr_is_enabled, "is_enabled")
    core.on("test_visibility.atr.session_has_failed_tests", _on_atr_session_has_failed_tests, "has_failed_tests")
    core.on("test_visibility.atr.should_retry_test", _on_atr_should_retry_test, "should_retry_test")
    core.on("test_visibility.atr.add_retry", _on_atr_add_retry, "retry_number")
    core.on("test_visibility.atr.start_retry", _on_atr_start_retry)
    core.on("test_visibility.atr.finish_retry", _on_atr_finish_retry)
    core.on("test_visibility.atr.get_final_status", _on_atr_get_final_status, "atr_final_status")


@_requires_civisibility_enabled
def _on_attempt_to_fix_should_retry_test(item_id: InternalTestId) -> bool:
    return CIVisibility.get_test_by_id(item_id).attempt_to_fix_should_retry()


@_requires_civisibility_enabled
def _on_attempt_to_fix_add_retry(item_id: InternalTestId, retry_number: int) -> Optional[int]:
    return CIVisibility.get_test_by_id(item_id).attempt_to_fix_add_retry(retry_number)


@_requires_civisibility_enabled
def _on_attempt_to_fix_start_retry(test_id: InternalTestId, retry_number: int) -> None:
    CIVisibility.get_test_by_id(test_id).attempt_to_fix_start_retry(retry_number)


@_requires_civisibility_enabled
def _on_attempt_to_fix_finish_retry(
    attempt_to_fix_finish_args: AttemptToFixTestMixin.AttemptToFixRetryFinishArgs,
) -> None:
    CIVisibility.get_test_by_id(attempt_to_fix_finish_args.test_id).attempt_to_fix_finish_retry(
        attempt_to_fix_finish_args.retry_number, attempt_to_fix_finish_args.status, attempt_to_fix_finish_args.exc_info
    )


@_requires_civisibility_enabled
def _on_attempt_to_fix_get_final_status(test_id: InternalTestId) -> TestStatus:
    return CIVisibility.get_test_by_id(test_id).attempt_to_fix_get_final_status()


def _register_attempt_to_fix_handlers() -> None:
    log.debug("Registering AttemptToFix handlers")
    core.on(
        "test_visibility.attempt_to_fix.should_retry_test", _on_attempt_to_fix_should_retry_test, "should_retry_test"
    )
    core.on("test_visibility.attempt_to_fix.add_retry", _on_attempt_to_fix_add_retry, "retry_number")
    core.on("test_visibility.attempt_to_fix.start_retry", _on_attempt_to_fix_start_retry)
    core.on("test_visibility.attempt_to_fix.finish_retry", _on_attempt_to_fix_finish_retry)
    core.on(
        "test_visibility.attempt_to_fix.get_final_status",
        _on_attempt_to_fix_get_final_status,
        "attempt_to_fix_final_status",
    )


class FeatureManager:
    """Manages CI Visibility feature flags"""
    
    def __init__(self, api_settings: TestVisibilityAPISettings):
        self._api_settings = api_settings
        
    def is_coverage_collection_enabled(self) -> bool:
        """Check if coverage collection is enabled"""
        return CIVisibility._should_collect_coverage(self._api_settings.coverage_enabled)
        
    def is_test_skipping_enabled(self) -> bool:
        """Check if test skipping is enabled"""
        return self._api_settings.skipping_enabled and self._api_settings.itr_enabled
        
    def is_itr_enabled(self) -> bool:
        """Check if Intelligent Test Runner is enabled"""
        return self._api_settings.itr_enabled
        
    def is_efd_enabled(self) -> bool:
        """Check if Early Flake Detection is enabled"""
        return self._api_settings.early_flake_detection.enabled

    def is_flaky_test_retries_enabled(self) -> bool:
        """Check if Auto Test Retries is enabled"""
        return self._api_settings.flaky_test_retries_enabled
        
    def log_settings(self) -> None:
        """Log the current feature settings"""
        log.debug("API-provided settings: coverage collection: %s", self._api_settings.coverage_enabled)
        log.debug(
            "API-provided settings: Intelligent Test Runner: %s, test skipping: %s",
            self._api_settings.itr_enabled,
            self._api_settings.skipping_enabled,
        )
        log.debug(
            "API-provided settings: Early Flake Detection enabled: %s",
            self._api_settings.early_flake_detection.enabled,
        )
        log.debug("API-provided settings: Auto Test Retries enabled: %s", self._api_settings.flaky_test_retries_enabled)


_register_session_handlers()
_register_module_handlers()
_register_suite_handlers()
_register_test_handlers()
_register_item_handlers()
_register_tag_handlers()
_register_coverage_handlers()
_register_itr_handlers()
_register_efd_handlers()
_register_atr_handlers()
_register_attempt_to_fix_handlers()
