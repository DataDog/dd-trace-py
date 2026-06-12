import atexit
import contextlib
import json
import logging
from pathlib import Path
import re
import time
import typing as t


try:
    import fcntl

    _FCNTL_AVAILABLE = True
except ImportError:  # pragma: no cover — Windows / restricted environments
    _FCNTL_AVAILABLE = False

from ddtrace.internal.settings import env
from ddtrace.testing.internal.api_client import APIClient
from ddtrace.testing.internal.cached_file_provider import CachedFileDataProvider
from ddtrace.testing.internal.cached_file_provider import TestOptDataProvider
from ddtrace.testing.internal.ci import CITag
from ddtrace.testing.internal.constants import DEFAULT_SERVICE_NAME
from ddtrace.testing.internal.constants import ITRSkippingLevel
from ddtrace.testing.internal.env_tags import get_env_tags
from ddtrace.testing.internal.git import Git
from ddtrace.testing.internal.git import GitTag
from ddtrace.testing.internal.http import BackendConnectorSetup
from ddtrace.testing.internal.http import NoOpBackendConnectorSetup
from ddtrace.testing.internal.offline_mode import get_offline_mode
from ddtrace.testing.internal.platform import get_platform_tags
from ddtrace.testing.internal.retry_handlers import AttemptToFixHandler
from ddtrace.testing.internal.retry_handlers import AutoTestRetriesHandler
from ddtrace.testing.internal.retry_handlers import EarlyFlakeDetectionHandler
from ddtrace.testing.internal.retry_handlers import RetryHandler
from ddtrace.testing.internal.settings_data import TestProperties
from ddtrace.testing.internal.telemetry import PayloadFileTelemetryAPI
from ddtrace.testing.internal.telemetry import TelemetryAPI
from ddtrace.testing.internal.test_data import SuiteRef
from ddtrace.testing.internal.test_data import Test
from ddtrace.testing.internal.test_data import TestModule
from ddtrace.testing.internal.test_data import TestRef
from ddtrace.testing.internal.test_data import TestSession
from ddtrace.testing.internal.test_data import TestSuite
from ddtrace.testing.internal.test_data import TestTag
from ddtrace.testing.internal.tracer_api import Codeowners
from ddtrace.testing.internal.utils import asbool
from ddtrace.testing.internal.writer import PayloadFileCoverageWriter
from ddtrace.testing.internal.writer import PayloadFileTestOptWriter
from ddtrace.testing.internal.writer import TestCoverageWriter
from ddtrace.testing.internal.writer import TestOptWriter


log = logging.getLogger(__name__)


# Sentinel written after a successful upload_git_data() so sibling xdist workers
# can skip re-running the same git probe + pack-objects + upload work.
_UPLOAD_SENTINEL_FILENAME = "dd-trace-py.upload-done"
_UPLOAD_SENTINEL_TTL_SECONDS = 300

# Cross-process lock so concurrent xdist workers don't all race into the same
# upload work; the holder writes the sentinel, peers see it on lock release.
_UPLOAD_LOCK_FILENAME = "dd-trace-py.upload.lock"
_UPLOAD_LOCK_TIMEOUT_SECONDS = 60.0
_UPLOAD_LOCK_RETRY_INTERVAL_SECONDS = 0.5


def _parse_line_number(value: str) -> t.Optional[int]:
    """Return the integer value of a source line tag, or None if it is not numeric."""
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


class SessionManager:
    def __init__(self, session: TestSession) -> None:
        offline = get_offline_mode()
        # NoOp connector only in Bazel payload-files mode: the hermetic sandbox has no
        # network access and all output goes to files. In ddtest manifest mode the
        # manifest file is present but the network IS available, so we use the real
        # connector to submit spans to the backend.
        if offline.payload_files_enabled:
            self.connector_setup: BackendConnectorSetup = NoOpBackendConnectorSetup()
        else:
            self.connector_setup = BackendConnectorSetup.detect_setup()

        telemetry_output_dir = offline.payload_output_dir("telemetry")
        if telemetry_output_dir is not None:
            self.telemetry_api: TelemetryAPI = PayloadFileTelemetryAPI(
                connector_setup=self.connector_setup, output_dir=telemetry_output_dir
            )
        else:
            self.telemetry_api = TelemetryAPI(connector_setup=self.connector_setup)

        self.env_tags = get_env_tags()
        if workspace_path := self.env_tags.get(CITag.WORKSPACE_PATH):
            self.workspace_path = Path(workspace_path)
        else:
            self.workspace_path = Path.cwd()

        self.platform_tags = get_platform_tags()
        self.collected_tests: set[TestRef] = set()
        self.skippable_items: set[t.Union[SuiteRef, TestRef]] = set()
        self.itr_correlation_id: t.Optional[str] = None
        self.itr_skipping_level = ITRSkippingLevel.TEST  # TODO: SUITE level not supported at the moment.

        self.is_user_provided_service: bool

        dd_service = env.get("DD_SERVICE")
        if dd_service:
            self.is_user_provided_service = True
            self.service = dd_service
        else:
            self.is_user_provided_service = False
            self.service = _get_service_name_from_git_repo(self.env_tags) or DEFAULT_SERVICE_NAME

        self.is_auto_injected = bool(env.get("DD_CIVISIBILITY_AUTO_INSTRUMENTATION_PROVIDER", ""))

        self.env = env.get("_CI_DD_ENV", env.get("DD_ENV", None))
        if self.env is None:
            self.env = self.connector_setup.default_env()

        self.api_client: TestOptDataProvider
        if offline.manifest_enabled:
            if offline.test_optimization_dir is None:  # pragma: no cover — invariant: always set with manifest_enabled
                raise RuntimeError("manifest_enabled is True but test_optimization_dir is None")
            self.api_client = CachedFileDataProvider(
                test_optimization_dir=offline.test_optimization_dir,
                itr_skipping_level=self.itr_skipping_level,
                telemetry_api=self.telemetry_api,
            )
        else:
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

        self.show_settings()

        self.known_tests = self.api_client.get_known_tests() if self.settings.known_tests_enabled else set()

        if asbool(env.get("DD_TEST_MANAGEMENT_ATF_ALL_FLAKY", "false")):
            tm_properties = self.api_client.get_all_flaky_test_management_properties()
            self.atf_all_flaky_tests: bool = True
            self.test_properties: dict[TestRef, TestProperties] = tm_properties
            log.info(
                "ATF-all-flaky mode: %d flaky tests will run with attempt_to_fix, all others will be skipped",
                len(tm_properties),
            )
        else:
            self.atf_all_flaky_tests = False
            self.test_properties = (
                self.api_client.get_test_management_properties() if self.settings.test_management.enabled else {}
            )

        self.upload_git_data()
        if self.settings.itr_enabled:
            self.skippable_items, self.itr_correlation_id = self.api_client.get_skippable_tests()
        else:
            self.skippable_items = set()
            self.itr_correlation_id = None
        if self.settings.require_git:
            # Fetch settings again after uploading git data, as it may change ITR settings.
            self.settings = self.api_client.get_settings()
            self.override_settings_with_env_vars()

        # Snapshot configuration errors before closing the client.
        self.configuration_errors = dict(self.api_client.configuration_errors)

        self.api_client.close()

        # Retry handlers must be set up after collection phase for EFD faulty session logic to work.
        self.retry_handlers: list[RetryHandler] = []

        tests_output_dir = offline.payload_output_dir("tests")
        if tests_output_dir is not None:
            self.writer: TestOptWriter = PayloadFileTestOptWriter(
                connector_setup=self.connector_setup, output_dir=tests_output_dir
            )
        else:
            self.writer = TestOptWriter(connector_setup=self.connector_setup)

        coverage_output_dir = offline.payload_output_dir("coverage")
        if coverage_output_dir is not None:
            self.coverage_writer: TestCoverageWriter = PayloadFileCoverageWriter(
                connector_setup=self.connector_setup, output_dir=coverage_output_dir
            )
        else:
            self.coverage_writer = TestCoverageWriter(connector_setup=self.connector_setup)
        self.session = session
        self.session.set_service(self.service)
        self.session.set_itr_attributes(
            itr_enabled=self.settings.itr_enabled,
            skipping_enabled=self.settings.skipping_enabled,
            skipping_level=self.itr_skipping_level,
        )

        # Propagate configuration errors to the session event and all child events.
        if self.configuration_errors:
            self.session.configuration_errors = self.configuration_errors
            self.session.set_tags(self.configuration_errors)

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
                # Ensure service.name is present in metadata["*"] so the uploader
                # does not overwrite it with values from the Bazel context.json.
                "service.name": self.service,
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
        self.collected_tests.clear()

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
                    log.debug("Not enabling Early Flake Detection: too many new tests")
                    self.session.set_early_flake_detection_abort_reason("faulty")
                else:
                    self.retry_handlers.append(EarlyFlakeDetectionHandler(self))
            else:
                log.debug("Not enabling Early Flake Detection: no known tests")

        if self.settings.auto_test_retries.enabled:
            self.retry_handlers.append(AutoTestRetriesHandler(self))

    def start(self) -> None:
        self.writer.start()
        self.coverage_writer.start()
        atexit.register(self.finish)

    def upload_coverage_report(
        self, coverage_report_bytes: bytes, coverage_format: str, tags: t.Optional[dict[str, str]] = None
    ) -> bool:
        """
        Upload a coverage report to Datadog CI Intake.

        This creates a temporary API client connection to upload the coverage report.

        Args:
            coverage_report_bytes: The coverage report content (will be gzipped by the API client)
            coverage_format: The format of the report (lcov, cobertura, jacoco, clover, opencover, simplecov)
            tags: Optional additional tags to include in the event

        Returns:
            True if upload succeeded, False otherwise
        """
        try:
            result = self.api_client.upload_coverage_report(coverage_report_bytes, coverage_format, tags)
            return result

        except Exception as e:
            log.warning("Error uploading coverage report: %s", e)
            return False

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
    ) -> tuple[TestModule, TestSuite, Test]:
        """
        Return the module, suite and test objects for a given test reference, creating them if necessary.

        When a new module, suite or test is discovered, the corresponding `on_new_*` callback is invoked. This can be
        used to perform test framework specific initialization (such as setting pathnames from data colleced by the
        framework).
        """
        test_module, created = self.session.get_or_create_child(test_ref.suite.module.name)
        if created:
            if self.configuration_errors:
                test_module.set_tags(self.configuration_errors)
            try:
                on_new_module(test_module)
            except Exception:
                log.warning("Error during discovery of module %s", test_module)

        test_suite, created = test_module.get_or_create_child(test_ref.suite.name)
        if created:
            if self.configuration_errors:
                test_suite.set_tags(self.configuration_errors)
            try:
                on_new_suite(test_suite)
            except Exception:
                log.warning("Error during discovery of suite %s", test_suite)

        test, created = test_suite.get_or_create_child(test_ref.name)
        if created:
            try:
                is_new = not test.has_parameters() and len(self.known_tests) > 0 and test_ref not in self.known_tests

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
                log.warning("Error during discovery of test %s", test)

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

    def _set_suite_source_location(self, suite: TestSuite) -> None:
        """Set test.source.file and test.source.start on a suite span.

        In pytest every suite maps to a single source file, so we only set these tags when all
        tests in the suite share the same file.  The start line is the minimum across all tests
        (i.e. the first test in the file); when no test carries a start line we fall back to 1
        so the UI can still link to the top of the file.
        """
        source_files = {sf for test in suite.children.values() if (sf := test.get_source_file())}
        if len(source_files) != 1:
            return

        (source_file,) = source_files
        suite.tags[TestTag.SOURCE_FILE] = source_file

        start_lines = [
            v
            for test in suite.children.values()
            if TestTag.SOURCE_START in test.tags
            if (v := _parse_line_number(test.tags[TestTag.SOURCE_START])) is not None
        ]
        suite.tags[TestTag.SOURCE_START] = str(min(start_lines)) if start_lines else "1"

        end_lines = [
            v
            for test in suite.children.values()
            if TestTag.SOURCE_END in test.tags
            if (v := _parse_line_number(test.tags[TestTag.SOURCE_END])) is not None
        ]
        if end_lines:
            suite.tags[TestTag.SOURCE_END] = str(max(end_lines))

    def _get_test_session_name(self) -> str:
        if session_name := env.get("DD_TEST_SESSION_NAME"):
            return session_name

        if job_name := self.env_tags.get(CITag.JOB_NAME):
            return f"{job_name}-{self.session.test_command}"

        return self.session.test_command

    def _upload_sentinel_path(self) -> t.Optional[Path]:
        """Return the path to the upload-completion sentinel, or None if unavailable.

        The sentinel lives inside ``.git/`` so it is naturally scoped to one repo
        checkout and shared between xdist workers running against the same workspace.
        """
        workspace_path = getattr(self, "workspace_path", None)
        if workspace_path is None:
            return None
        return Path(workspace_path) / ".git" / _UPLOAD_SENTINEL_FILENAME

    def _read_upload_sentinel(self) -> t.Optional[dict[str, t.Any]]:
        """Return the sentinel payload if it is fresh and matches our HEAD, else None.

        Matches require the sentinel to be present, well-formed JSON, written within
        the last ``_UPLOAD_SENTINEL_TTL_SECONDS`` seconds, and to reference the same
        commit SHA we are about to upload for. Anything else is treated as no match,
        so the worker falls through to the normal upload path.
        """
        env_tags = getattr(self, "env_tags", None) or {}
        head_sha = env_tags.get(GitTag.COMMIT_SHA)
        if not head_sha:
            return None
        sentinel = self._upload_sentinel_path()
        if sentinel is None:
            return None
        try:
            data = json.loads(sentinel.read_text())
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or data.get("head_sha") != head_sha:
            return None
        timestamp = data.get("timestamp")
        if not isinstance(timestamp, (int, float)):
            return None
        if time.time() - timestamp > _UPLOAD_SENTINEL_TTL_SECONDS:
            return None
        return data

    def _apply_upload_sentinel(self, data: dict[str, t.Any]) -> None:
        """Recover state from a sibling worker's sentinel into our own env_tags.

        Currently this carries the PR merge-base SHA: a worker that skips its own
        ``upload_git_data`` would otherwise never call ``_update_pr_merge_base``, so
        its events would be missing ``git.pull_request.base_branch_sha``.
        Pre-existing values (e.g. user-supplied env vars) take precedence.
        """
        merge_base = data.get("merge_base_sha")
        if isinstance(merge_base, str) and merge_base and not self.env_tags.get(GitTag.PULL_REQUEST_BASE_BRANCH_SHA):
            self.env_tags[GitTag.PULL_REQUEST_BASE_BRANCH_SHA] = merge_base

    def _mark_upload_done(self) -> None:
        """Persist a sentinel so sibling xdist workers can skip upload_git_data."""
        env_tags = getattr(self, "env_tags", None) or {}
        head_sha = env_tags.get(GitTag.COMMIT_SHA)
        if not head_sha:
            return
        sentinel = self._upload_sentinel_path()
        if sentinel is None:
            return
        payload_data: dict[str, t.Any] = {"head_sha": head_sha, "timestamp": time.time()}
        merge_base = env_tags.get(GitTag.PULL_REQUEST_BASE_BRANCH_SHA)
        if merge_base:
            payload_data["merge_base_sha"] = merge_base
        tmp = sentinel.with_suffix(sentinel.suffix + ".tmp")
        try:
            tmp.write_text(json.dumps(payload_data))
            tmp.replace(sentinel)
        except OSError as e:
            log.debug("Could not write git upload sentinel %s: %s", sentinel, e)

    def _upload_lock_path(self) -> t.Optional[Path]:
        """Return the path to the cross-process upload lock, or None if unavailable."""
        workspace_path = getattr(self, "workspace_path", None)
        if workspace_path is None:
            return None
        return Path(workspace_path) / ".git" / _UPLOAD_LOCK_FILENAME

    def cleanup_upload_artifacts(self) -> None:
        """Delete the upload sentinel and lock files left in .git/.

        Safe to call once the session is finishing — by that point all workers
        have already run upload_git_data() during their __init__, so neither
        file is needed any more. Should be called only from the controller
        process (not xdist workers) so we don't race with a slow-starting peer.
        """
        for path in (self._upload_sentinel_path(), self._upload_lock_path()):
            if path is None:
                continue
            try:
                path.unlink(missing_ok=True)
            except OSError as e:
                log.debug("Could not remove upload artifact %s: %s", path, e)

    @contextlib.contextmanager
    def _upload_lock(self) -> t.Iterator[bool]:
        """Acquire a cross-process exclusive lock around upload_git_data.

        Yields ``True`` if this process is the elected uploader. This covers
        two cases: the lock was acquired successfully, or lock infrastructure
        is unavailable (no fcntl, no writable ``.git/`` directory, flock error)
        — in those cases no coordination is possible so we proceed as sole uploader.

        Yields ``False`` only when the lock timeout is exceeded, meaning a peer
        process is alive and actively holding the lock. Crashed processes release
        flock locks immediately on fd close, so timeout == live peer uploading.
        The caller should skip the upload in that case.

        **Windows note**: ``fcntl`` is not available on Windows, so this lock is
        a no-op there (always yields ``True``). Cross-worker coordination on
        Windows relies solely on the upload sentinel file: workers that arrive
        after the first successful upload see the sentinel and skip. Workers that
        all start simultaneously may each attempt an upload, but the backend
        deduplicates commits so duplicate uploads are wasteful but not incorrect.
        If full coordination on Windows becomes necessary, replacing this with a
        ``filelock``-based implementation would be the cleanest path.
        """
        if not _FCNTL_AVAILABLE:
            yield True
            return

        lock_path = self._upload_lock_path()
        if lock_path is None:
            yield True
            return

        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            log.debug("Could not create directory for upload lock %s: %s", lock_path, e)
            yield True
            return

        try:
            lock_file = open(lock_path, "w")
        except OSError as e:
            log.debug("Could not open upload lock %s: %s", lock_path, e)
            yield True
            return

        try:
            deadline = time.monotonic() + _UPLOAD_LOCK_TIMEOUT_SECONDS
            while True:
                try:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        log.debug(
                            "Upload lock timeout after %ss, skipping upload (peer is uploading)",
                            _UPLOAD_LOCK_TIMEOUT_SECONDS,
                        )
                        yield False
                        return
                    time.sleep(_UPLOAD_LOCK_RETRY_INTERVAL_SECONDS)
                except OSError as e:
                    log.debug("flock on %s failed: %s", lock_path, e)
                    yield True
                    return
            try:
                yield True
            finally:
                try:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
        finally:
            lock_file.close()

    def _update_pr_merge_base(self, git: Git) -> None:
        """Compute PULL_REQUEST_BASE_BRANCH_SHA via git merge-base if not already set.

        Called from upload_git_data() after any unshallowing so the commits are
        guaranteed to be present in the local history.
        """
        if self.env_tags.get(GitTag.PULL_REQUEST_BASE_BRANCH_SHA):
            return
        base_sha = self.env_tags.get(GitTag.PULL_REQUEST_BASE_BRANCH_HEAD_SHA)
        head_sha = self.env_tags.get(GitTag.COMMIT_HEAD_SHA)
        if base_sha and head_sha:
            if merge_base := git.get_merge_base(base_sha, head_sha):
                self.env_tags[GitTag.PULL_REQUEST_BASE_BRANCH_SHA] = merge_base

    def upload_git_data(self) -> None:
        # NOTE: In manifest mode (Bazel sandbox), git commands are unavailable
        # and the external tool has already uploaded git pack data before the sandbox
        # was created. Skip unconditionally to avoid subprocess failures.
        offline = get_offline_mode()
        if offline.manifest_enabled or offline.payload_files_enabled:
            log.debug("Skipping git data upload in offline/payload-files mode")
            return

        # Fast path: a sibling worker already uploaded for this HEAD.
        if (sentinel_data := self._read_upload_sentinel()) is not None:
            log.debug("Git data upload already completed by another worker for this HEAD, skipping")
            self._apply_upload_sentinel(sentinel_data)
            return

        # Cross-process lock: only one xdist worker per workspace runs the upload at a time.
        with self._upload_lock() as lock_acquired:
            if not lock_acquired:
                # Timed out — a peer is alive and still uploading (crashed processes
                # release flock locks immediately on fd close).
                if (sentinel_data := self._read_upload_sentinel()) is not None:
                    # Peer finished just before our timeout fired — recover merge-base.
                    self._apply_upload_sentinel(sentinel_data)
                else:
                    # Peer is still uploading; sentinel not yet written. Compute the
                    # merge-base directly so this worker's events aren't missing
                    # git.pull_request.base_branch_sha. This is a single git command
                    # (no upload, no lock needed).
                    try:
                        self._update_pr_merge_base(Git())
                    except RuntimeError:
                        log.debug("git binary unavailable, skipping merge-base computation on lock timeout")
                return
            # Re-check the sentinel: a peer may have finished while we waited for the lock.
            if (sentinel_data := self._read_upload_sentinel()) is not None:
                log.debug("Git data upload completed by peer while waiting for lock, skipping")
                self._apply_upload_sentinel(sentinel_data)
                return
            self._do_upload_git_data()

    def _do_upload_git_data(self) -> None:
        """Run the actual upload flow; the caller should already hold the upload lock."""
        # Missing `git` in minimal containers should not abort pytest startup.
        try:
            git = Git()
        except RuntimeError:
            log.warning("Error calling git binary, skipping metadata upload")
            if TelemetryAPI._instance is not None:
                TelemetryAPI.get().record_git_missing()
            return

        latest_commits = git.get_latest_commits()
        backend_commits = self.api_client.get_known_commits(latest_commits)
        if backend_commits is None:
            log.warning("search_commits failed, aborting git metadata upload")
            TelemetryAPI.get().record_git_pack_data(0, 0)
            return

        commits_not_in_backend = list(set(latest_commits) - set(backend_commits))

        if git.is_shallow_repository() and git.get_git_version() >= (2, 27, 0):
            log.debug("Shallow repository detected on git > 2.27 detected, unshallowing")
            unshallow_successful = git.try_all_unshallow_repository_methods()
            if unshallow_successful:
                if len(commits_not_in_backend) > 0:
                    log.debug("Unshallow successful, getting latest commits from backend based on unshallowed commits")
                    latest_commits = git.get_latest_commits()
                    backend_commits = self.api_client.get_known_commits(latest_commits)
                    if backend_commits is None:
                        log.warning("search_commits failed after unshallow, aborting git metadata upload")
                        TelemetryAPI.get().record_git_pack_data(0, 0)
                        return

                    commits_not_in_backend = list(set(latest_commits) - set(backend_commits))
            elif len(commits_not_in_backend) > 0:
                log.warning("Failed to unshallow repository, continuing to send pack data")

        # Compute the true PR merge-base now that the repo has been unshallowed (if needed).
        # This is done here rather than at env_tags collection time because on shallow clones the
        # required commits are only available after the fetch above completes.
        # Must run before the early-return below so sessions where all commits are already
        # known to the backend still populate git.pull_request.base_branch_sha.
        self._update_pr_merge_base(git)

        if len(commits_not_in_backend) == 0:
            log.debug("All latest commits found in backend, skipping metadata upload")
            self._mark_upload_done()
            return

        revisions_to_send = git.get_filtered_revisions(
            excluded_commits=backend_commits, included_commits=commits_not_in_backend
        )

        uploaded_files = 0
        failed_files = 0
        uploaded_bytes = 0

        for packfile in git.pack_objects(revisions_to_send):
            nbytes = self.api_client.send_git_pack_file(packfile)
            if nbytes is not None:
                uploaded_bytes += nbytes
                uploaded_files += 1
            else:
                failed_files += 1

        TelemetryAPI.get().record_git_pack_data(uploaded_files, uploaded_bytes)
        # Only write the sentinel when every packfile succeeded. Partial success
        # (some packfiles sent, others failed) must not suppress peer retries —
        # the missing packs would never be uploaded if we did.
        if failed_files > 0:
            log.debug("%d packfile(s) failed to upload; leaving sentinel unset so peers can retry", failed_files)
        elif uploaded_files > 0:
            self._mark_upload_done()
        else:
            log.debug("No packfiles uploaded successfully; leaving sentinel unset so peers can retry")

    def is_skippable_test(self, test_ref: TestRef) -> bool:
        if not self.settings.skipping_enabled:
            return False

        return test_ref in self.skippable_items or test_ref.suite in self.skippable_items

    def has_codeowners(self) -> bool:
        return self.codeowners is not None

    def override_settings_with_env_vars(self) -> None:
        # Kill switches.
        # These variables default to true, and if explicitly given a false value, disable a feature.
        if not asbool(env.get("DD_CIVISIBILITY_ITR_ENABLED", "true")):
            log.debug("Test Impact Analysis is disabled by environment variable")
            self.settings.itr_enabled = False

        if not asbool(env.get("DD_CIVISIBILITY_EARLY_FLAKE_DETECTION_ENABLED", "true")):
            log.debug("Early Flake Detection is disabled by environment variable")
            self.settings.early_flake_detection.enabled = False

        if not asbool(env.get("DD_CIVISIBILITY_FLAKY_RETRY_ENABLED", "true")):
            log.debug("Auto Test Retries is disabled by environment variable")
            self.settings.auto_test_retries.enabled = False

        if not asbool(env.get("DD_TEST_MANAGEMENT_ENABLED", "true")):
            log.debug("Test Management is disabled by environment variable")
            self.settings.test_management.enabled = False

        if asbool(env.get("DD_TEST_MANAGEMENT_ATF_ALL_FLAKY", "false")):
            log.debug("ATF-all-flaky mode enabled: forcing Test Management on")
            self.settings.test_management.enabled = True

        _coverage_upload_env = env.get("DD_CIVISIBILITY_CODE_COVERAGE_REPORT_UPLOAD_ENABLED", "")
        if _coverage_upload_env.lower() in ("false", "0"):
            log.debug("Coverage report upload is disabled by environment variable")
            self.settings.coverage_report_upload_enabled = False

        # "Reverse" kill switches.
        # These variables default to false, and if explicitly given a true value, disable a feature.
        if asbool(env.get("_DD_CIVISIBILITY_ITR_PREVENT_TEST_SKIPPING", "false")):
            log.debug("TIA test skipping is disabled by environment variable")
            self.settings.skipping_enabled = False

        # Other overrides.
        # These variables default to false, and if explicitly given a true value, enable a feature.
        if asbool(env.get("_DD_CIVISIBILITY_ITR_FORCE_ENABLE_COVERAGE", "false")):
            log.debug("TIA code coverage collection is enabled by environment variable")
            self.settings.coverage_enabled = True

        if asbool(env.get("DD_CIVISIBILITY_CODE_COVERAGE_REPORT_UPLOAD_ENABLED", "false")):
            log.debug("Code coverage report upload is enabled by environment variable")
            self.settings.coverage_report_upload_enabled = True

    def show_settings(self) -> None:
        log.info("Service: %s (env: %s)", self.service, self.env)
        log.info(
            "Test Optimization settings: Test Impact Analysis: %s, test skipping: %s, coverage collection: %s",
            self.settings.itr_enabled,
            self.settings.skipping_enabled,
            self.settings.coverage_enabled,
        )
        log.info(
            "Test Optimization settings: Early Flake Detection enabled: %s", self.settings.early_flake_detection.enabled
        )
        log.info("Test Optimization settings: Known Tests enabled: %s", self.settings.known_tests_enabled)
        log.info("Test Optimization settings: Auto Test Retries enabled: %s", self.settings.auto_test_retries.enabled)
        log.info(
            "Test Optimization settings: Coverage Report Upload enabled: %s",
            self.settings.coverage_report_upload_enabled,
        )


def _get_service_name_from_git_repo(env_tags: dict[str, str]) -> t.Optional[str]:
    repo_name = env_tags.get(GitTag.REPOSITORY_URL)
    if repo_name and (m := re.match(r".*/([^/]+)(?:.git)/?", repo_name)):
        return m.group(1).lower()
    else:
        return None
