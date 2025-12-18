import atexit
import logging
import os
from pathlib import Path
import re
import typing as t

from ddtrace.testing.internal.api_client import APIClient
from ddtrace.testing.internal.ci import CITag
from ddtrace.testing.internal.constants import DEFAULT_ENV_NAME
from ddtrace.testing.internal.constants import DEFAULT_SERVICE_NAME
from ddtrace.testing.internal.env_tags import get_env_tags
from ddtrace.testing.internal.git import Git
from ddtrace.testing.internal.git import GitTag
from ddtrace.testing.internal.http import BackendConnectorSetup
from ddtrace.testing.internal.platform import get_platform_tags
from ddtrace.testing.internal.retry_handlers import AttemptToFixHandler
from ddtrace.testing.internal.retry_handlers import AutoTestRetriesHandler
from ddtrace.testing.internal.retry_handlers import EarlyFlakeDetectionHandler
from ddtrace.testing.internal.retry_handlers import RetryHandler
from ddtrace.testing.internal.settings_data import TestProperties
from ddtrace.testing.internal.telemetry import TelemetryAPI
from ddtrace.testing.internal.test_data import ITRSkippingLevel
from ddtrace.testing.internal.test_data import SuiteRef
from ddtrace.testing.internal.test_data import Test
from ddtrace.testing.internal.test_data import TestModule
from ddtrace.testing.internal.test_data import TestRef
from ddtrace.testing.internal.test_data import TestSession
from ddtrace.testing.internal.test_data import TestSuite
from ddtrace.testing.internal.test_data import TestTag
from ddtrace.testing.internal.tracer_api import Codeowners
from ddtrace.testing.internal.utils import asbool
from ddtrace.testing.internal.writer import TestCoverageWriter
from ddtrace.testing.internal.writer import TestOptWriter


log = logging.getLogger(__name__)


class SessionManager:
    def __init__(self, session: TestSession) -> None:
        self.connector_setup = BackendConnectorSetup.detect_setup()
        self.telemetry_api = TelemetryAPI(connector_setup=self.connector_setup)

        self.env_tags = get_env_tags()
        if workspace_path := self.env_tags.get(CITag.WORKSPACE_PATH):
            self.workspace_path = Path(workspace_path)
        else:
            self.workspace_path = Path.cwd()

        self.platform_tags = get_platform_tags()
        self.collected_tests: t.Set[TestRef] = set()
        self.skippable_items: t.Set[t.Union[SuiteRef, TestRef]] = set()
        self.itr_correlation_id: t.Optional[str] = None
        self.itr_skipping_level = ITRSkippingLevel.TEST  # TODO: SUITE level not supported at the moment.

        self.is_user_provided_service: bool

        dd_service = os.environ.get("DD_SERVICE")
        if dd_service:
            self.is_user_provided_service = True
            self.service = dd_service
        else:
            self.is_user_provided_service = False
            self.service = _get_service_name_from_git_repo(self.env_tags) or DEFAULT_SERVICE_NAME

        self.is_auto_injected = bool(os.getenv("DD_CIVISIBILITY_AUTO_INSTRUMENTATION_PROVIDER", ""))

        self.env = os.environ.get("_CI_DD_ENV") or os.environ.get("DD_ENV") or DEFAULT_ENV_NAME

        self.api_client = APIClient(
            service=self.service,
            env=self.env,
            env_tags=self.env_tags,
            itr_skipping_level=self.itr_skipping_level,
            configurations=self.platform_tags,
            connector_setup=self.connector_setup,
            telemetry_api=self.telemetry_api,
        )
        self.settings = self.api_client.get_settings()
        self.override_settings_with_env_vars()

        self.known_tests = self.api_client.get_known_tests() if self.settings.known_tests_enabled else set()
        self.test_properties = (
            self.api_client.get_test_management_properties() if self.settings.test_management.enabled else {}
        )

        self.upload_git_data()
        self.skippable_items, self.itr_correlation_id = self.api_client.get_skippable_tests()
        if self.settings.require_git:
            # Fetch settings again after uploading git data, as it may change ITR settings.
            self.settings = self.api_client.get_settings()

        self.api_client.close()

        # Retry handlers must be set up after collection phase for EFD faulty session logic to work.
        self.retry_handlers: t.List[RetryHandler] = []

        self.writer = TestOptWriter(connector_setup=self.connector_setup)
        self.coverage_writer = TestCoverageWriter(connector_setup=self.connector_setup)
        self.session = session
        self.session.set_service(self.service)

        self.writer.add_metadata("*", self.env_tags)
        self.writer.add_metadata("*", self.platform_tags)
        self.writer.add_metadata(
            "*",
            {
                TestTag.TEST_COMMAND: self.session.test_command,
                TestTag.TEST_FRAMEWORK: self.session.test_framework,
                TestTag.TEST_FRAMEWORK_VERSION: self.session.test_framework_version,
                TestTag.TEST_SESSION_NAME: self._get_test_session_name(),
                TestTag.COMPONENT: self.session.test_framework,
                TestTag.ENV: self.env,
            },
        )

        if self.itr_correlation_id:
            itr_event = "test" if self.itr_skipping_level == ITRSkippingLevel.TEST else "test_suite_end"
            self.writer.add_metadata(itr_event, {"itr_correlation_id": self.itr_correlation_id})

        self.codeowners: t.Optional[Codeowners] = None

        try:
            self.codeowners = Codeowners(cwd=str(self.workspace_path))
        except ValueError:
            log.warning("CODEOWNERS file is not available")
        except Exception:
            log.warning("Failed to load CODEOWNERS", exc_info=True)

    def finish_collection(self) -> None:
        self.setup_retry_handlers()

    def setup_retry_handlers(self) -> None:
        if self.settings.test_management.enabled:
            self.retry_handlers.append(AttemptToFixHandler(self))

        if self.settings.early_flake_detection.enabled:
            if self.known_tests:
                # TODO: handle parametrized tests specially. Currently each parametrized version is counted as a
                # separate test.
                new_tests = self.collected_tests - self.known_tests
                total_tests = len(new_tests) + len(self.known_tests)
                new_tests_percentage = len(new_tests) / total_tests * 100
                is_faulty_session = (
                    len(new_tests) > self.settings.early_flake_detection.faulty_session_threshold
                    and new_tests_percentage > self.settings.early_flake_detection.faulty_session_threshold
                )
                if is_faulty_session:
                    log.info("Not enabling Early Flake Detection: too many new tests")
                    self.session.set_early_flake_detection_abort_reason("faulty")
                else:
                    self.retry_handlers.append(EarlyFlakeDetectionHandler(self))
            else:
                log.info("Not enabling Early Flake Detection: no known tests")

        if self.settings.auto_test_retries.enabled:
            self.retry_handlers.append(AutoTestRetriesHandler(self))

    def start(self) -> None:
        self.writer.start()
        self.coverage_writer.start()
        atexit.register(self.finish)

    def finish(self) -> None:
        # Avoid being called again by atexit if we've already been called by the pytest plugin.
        atexit.unregister(self.finish)

        # Start writer shutdown in background, so both can do it at the same time.
        self.writer.signal_finish()
        self.coverage_writer.signal_finish()

        # Telemetry API is based on ddtrace, we don't have fine-grained control over the background process.
        self.telemetry_api.finish()

        # Wait for the writer threads to finish.
        self.writer.wait_finish()
        self.coverage_writer.wait_finish()

    def discover_test(
        self,
        test_ref: TestRef,
        on_new_module: t.Callable[[TestModule], None],
        on_new_suite: t.Callable[[TestSuite], None],
        on_new_test: t.Callable[[Test], None],
    ) -> t.Tuple[TestModule, TestSuite, Test]:
        """
        Return the module, suite and test objects for a given test reference, creating them if necessary.

        When a new module, suite or test is discovered, the corresponding `on_new_*` callback is invoked. This can be
        used to perform test framework specific initialization (such as setting pathnames from data colleced by the
        framework).
        """
        test_module, created = self.session.get_or_create_child(test_ref.suite.module.name)
        if created:
            try:
                on_new_module(test_module)
            except Exception:
                log.exception("Error during discovery of module %s", test_module)

        test_suite, created = test_module.get_or_create_child(test_ref.suite.name)
        if created:
            try:
                on_new_suite(test_suite)
            except Exception:
                log.exception("Error during discovery of suite %s", test_suite)

        test, created = test_suite.get_or_create_child(test_ref.name)
        if created:
            try:
                self.collected_tests.add(test_ref)
                is_new = len(self.known_tests) > 0 and test_ref not in self.known_tests
                test_properties = self.test_properties.get(test_ref) or TestProperties()
                test.set_attributes(
                    is_new=is_new,
                    is_quarantined=test_properties.quarantined,
                    is_disabled=test_properties.disabled,
                    is_attempt_to_fix=test_properties.attempt_to_fix,
                )
                on_new_test(test)
                self._set_codeowners(test)
            except Exception:
                log.exception("Error during discovery of test %s", test)

        return test_module, test_suite, test

    def get_test(self, test_ref: TestRef) -> t.Optional[Test]:
        module = self.session.children.get(test_ref.suite.module.name)
        if not module:
            return None

        suite = module.children.get(test_ref.suite.name)
        if not suite:
            return None

        test = suite.children.get(test_ref.name)
        if not test:
            return None

        return test

    def _set_codeowners(self, test: Test) -> None:
        if not self.codeowners:
            return

        source_file = test.get_source_file()
        if not source_file:
            log.debug("Could not get source file for test %s", test)
            return

        try:
            repo_relative_path = str(Path(source_file).relative_to(self.workspace_path))
        except ValueError:
            log.debug("Could not get repo relative path for %r", source_file)
            repo_relative_path = source_file

        codeowners = self.codeowners.of(repo_relative_path)
        test.set_codeowners(codeowners)

    def _get_test_session_name(self) -> str:
        if session_name := os.environ.get("DD_TEST_SESSION_NAME"):
            return session_name

        if job_name := self.env_tags.get(CITag.JOB_NAME):
            return f"{job_name}-{self.session.test_command}"

        return self.session.test_command

    def upload_git_data(self) -> None:
        git = Git()
        latest_commits = git.get_latest_commits()
        backend_commits = self.api_client.get_known_commits(latest_commits)
        # TODO: ddtrace has a "backend_commits is None" logic here with early return (is it correct?).
        commits_not_in_backend = list(set(latest_commits) - set(backend_commits))

        if len(commits_not_in_backend) == 0:
            log.debug("All latest commits found in backend, skipping metadata upload")
            return

        if git.is_shallow_repository() and git.get_git_version() >= (2, 27, 0):
            log.debug("Shallow repository detected on git > 2.27 detected, unshallowing")
            unshallow_successful = git.try_all_unshallow_repository_methods()
            if unshallow_successful:
                log.debug("Unshallow successful, getting latest commits from backend based on unshallowed commits")
                latest_commits = git.get_latest_commits()
                backend_commits = self.api_client.get_known_commits(latest_commits)
                # TODO: ddtrace has a "backend_commits is None" logic here with early return (is it correct?).
                commits_not_in_backend = list(set(latest_commits) - set(backend_commits))
            else:
                log.warning("Failed to unshallow repository, continuing to send pack data")

        revisions_to_send = git.get_filtered_revisions(
            excluded_commits=backend_commits, included_commits=commits_not_in_backend
        )

        uploaded_files = 0
        uploaded_bytes = 0

        for packfile in git.pack_objects(revisions_to_send):
            nbytes = self.api_client.send_git_pack_file(packfile)
            if nbytes is not None:
                uploaded_bytes += nbytes
                uploaded_files += 1

        TelemetryAPI.get().record_git_pack_data(uploaded_files, uploaded_bytes)

    def is_skippable_test(self, test_ref: TestRef) -> bool:
        if not self.settings.skipping_enabled:
            return False

        return test_ref in self.skippable_items or test_ref.suite in self.skippable_items

    def has_codeowners(self) -> bool:
        return self.codeowners is not None

    def override_settings_with_env_vars(self) -> None:
        # Kill switches.
        # These variables default to true, and if explicitly given a false value, disable a feature.
        if not asbool(os.environ.get("DD_CIVISIBILITY_ITR_ENABLED", "true")):
            log.debug("Test Impact Analysis is disabled by environment variable")
            self.settings.itr_enabled = False

        if not asbool(os.environ.get("DD_CIVISIBILITY_EARLY_FLAKE_DETECTION_ENABLED", "true")):
            log.debug("Early Flake Detection is disabled by environment variable")
            self.settings.early_flake_detection.enabled = False

        if not asbool(os.environ.get("DD_CIVISIBILITY_FLAKY_RETRY_ENABLED", "true")):
            log.debug("Auto Test Retries is disabled by environment variable")
            self.settings.auto_test_retries.enabled = False

        # "Reverse" kill switches.
        # These variables default to false, and if explicitly given a true value, disable a feature.
        if asbool(os.environ.get("_DD_CIVISIBILITY_ITR_PREVENT_TEST_SKIPPING", "false")):
            log.debug("TIA test skipping is disabled by environment variable")
            self.settings.skipping_enabled = False

        # Other overrides.
        # These variables default to false, and if explicitly given a true value, enable a feature.
        if asbool(os.environ.get("_DD_CIVISIBILITY_ITR_FORCE_ENABLE_COVERAGE", "false")):
            log.debug("TIA code coverage collection is enabled by environment variable")
            self.settings.coverage_enabled = True


def _get_service_name_from_git_repo(env_tags: t.Dict[str, str]) -> t.Optional[str]:
    repo_name = env_tags.get(GitTag.REPOSITORY_URL)
    if repo_name and (m := re.match(r".*/([^/]+)(?:.git)/?", repo_name)):
        return m.group(1).lower()
    else:
        return None
