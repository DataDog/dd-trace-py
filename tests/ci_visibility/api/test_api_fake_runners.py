import inspect
import subprocess
from unittest import mock

import pytest

from ddtrace.internal.ci_visibility.recorder import _CIVisibilitySettings
from tests import utils
from tests.utils import TracerTestCase
from tests.utils import override_env
from tests.utils import snapshot


SNAPSHOT_IGNORES = [
    "meta.ci.workspace_path",
    "meta.error.stack",
    "meta.library_version",
    "meta.os.architecture",
    "meta.os.platform",
    "meta.os.version",
    "meta.runtime-id",
    "meta.runtime.version",
    "meta.test.framework_version",
    "meta.test_module_id",
    "meta.test_session_id",
    "meta.test_suite_id",
    "metrics._dd.top_level",
    "metrics._dd.tracer_kr",
    "metrics._sampling_priority_v1",
    "metrics.process_id",
    "duration",
    "start",
]


class FakeApiRunnersSnapshotTestCase(TracerTestCase):
    @pytest.fixture(autouse=True)
    def fixtures(self, testdir, monkeypatch, git_repo):
        self.testdir = testdir
        self.monkeypatch = monkeypatch
        self.git_repo = git_repo
        yield utils.git_repo_empty(self.testdir)

    @snapshot(ignores=SNAPSHOT_IGNORES)
    def test_manual_api_fake_runner_all_pass(self):
        import fake_runner_all_pass

        fake_runner_src = inspect.getsource(fake_runner_all_pass)
        self.testdir.makepyfile(fake_runner_all_pass=fake_runner_src)
        self.testdir.chdir()

        with override_env(
            dict(
                DD_API_KEY="foobar.baz",
            )
        ):
            with mock.patch(
                "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_settings_api",
                return_value=_CIVisibilitySettings(False, False, False, False),
            ):
                subprocess.run(["python", "fake_runner_all_pass.py"])

    @snapshot(ignores=SNAPSHOT_IGNORES)
    def test_manual_api_fake_runner_all_fail(self):
        import fake_runner_all_fail

        fake_runner_src = inspect.getsource(fake_runner_all_fail)
        self.testdir.makepyfile(fake_runner_all_fail=fake_runner_src)
        self.testdir.chdir()

        with override_env(
            dict(
                DD_API_KEY="foobar.baz",
            )
        ):
            with mock.patch(
                "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_settings_api",
                return_value=_CIVisibilitySettings(False, False, False, False),
            ):
                subprocess.run(["python", "fake_runner_all_fail.py"])

    @snapshot(ignores=SNAPSHOT_IGNORES)
    def test_manual_api_fake_runner_all_skip(self):
        import fake_runner_all_skip

        fake_runner_src = inspect.getsource(fake_runner_all_skip)
        self.testdir.makepyfile(fake_runner_all_skip=fake_runner_src)
        self.testdir.chdir()

        with override_env(
            dict(
                DD_API_KEY="foobar.baz",
            )
        ):
            with mock.patch(
                "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_settings_api",
                return_value=_CIVisibilitySettings(False, False, False, False),
            ):
                subprocess.run(["python", "fake_runner_all_skip.py"])

    @snapshot(ignores=SNAPSHOT_IGNORES)
    def test_manual_api_fake_runner_all_itr_skip_test_level(self):
        import fake_runner_all_itr_skip_test_level

        fake_runner_src = inspect.getsource(fake_runner_all_itr_skip_test_level)
        self.testdir.makepyfile(fake_runner_all_itr_skip_test_level=fake_runner_src)
        self.testdir.chdir()

        with override_env(
            dict(
                DD_API_KEY="foobar.baz",
            )
        ):
            with mock.patch(
                "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_settings_api",
                return_value=_CIVisibilitySettings(False, False, False, False),
            ):
                subprocess.run(["python", "fake_runner_all_itr_skip_test_level.py"])

    @snapshot(ignores=SNAPSHOT_IGNORES)
    def test_manual_api_fake_runner_all_itr_skip_suite_level(self):
        import fake_runner_all_itr_skip_suite_level

        fake_runner_src = inspect.getsource(fake_runner_all_itr_skip_suite_level)
        self.testdir.makepyfile(fake_runner_all_itr_skip_suite_level=fake_runner_src)
        self.testdir.chdir()

        with override_env(
            dict(
                DD_API_KEY="foobar.baz",
            )
        ):
            with mock.patch(
                "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_settings_api",
                return_value=_CIVisibilitySettings(False, False, False, False),
            ):
                subprocess.run(["python", "fake_runner_all_itr_skip_suite_level.py"])

    @snapshot(ignores=SNAPSHOT_IGNORES)
    def test_manual_api_fake_runner_mix_pass(self):
        import fake_runner_mix_pass

        fake_runner_src = inspect.getsource(fake_runner_mix_pass)
        self.testdir.makepyfile(fake_runner_mix_pass=fake_runner_src)
        self.testdir.chdir()

        with override_env(
            dict(
                DD_API_KEY="foobar.baz",
            )
        ):
            with mock.patch(
                "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_settings_api",
                return_value=_CIVisibilitySettings(False, False, False, False),
            ):
                subprocess.run(["python", "fake_runner_mix_pass.py"])

    @snapshot(ignores=SNAPSHOT_IGNORES)
    def test_manual_api_fake_runner_mix_fail(self):
        import fake_runner_mix_fail

        fake_runner_src = inspect.getsource(fake_runner_mix_fail)
        self.testdir.makepyfile(fake_runner_mix_fail=fake_runner_src)
        self.testdir.chdir()

        with override_env(
            dict(
                DD_API_KEY="foobar.baz",
            )
        ):
            with mock.patch(
                "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_settings_api",
                return_value=_CIVisibilitySettings(False, False, False, False),
            ):
                subprocess.run(["python", "fake_runner_mix_fail.py"])

    @snapshot(ignores=SNAPSHOT_IGNORES)
    def test_manual_api_fake_runner_mix_fail_itr_test_level(self):
        import fake_runner_mix_fail_itr_test_level

        fake_runner_src = inspect.getsource(fake_runner_mix_fail_itr_test_level)
        self.testdir.makepyfile(fake_runner_mix_fail_itr_test_level=fake_runner_src)
        self.testdir.chdir()

        with override_env(
            dict(
                DD_API_KEY="foobar.baz",
            )
        ):
            with mock.patch(
                "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_settings_api",
                return_value=_CIVisibilitySettings(False, False, False, False),
            ):
                subprocess.run(["python", "fake_runner_mix_fail_itr_test_level.py"])

    @snapshot(ignores=SNAPSHOT_IGNORES)
    def test_manual_api_fake_runner_mix_fail_itr_suite_level(self):
        import fake_runner_mix_fail_itr_suite_level

        fake_runner_src = inspect.getsource(fake_runner_mix_fail_itr_suite_level)
        self.testdir.makepyfile(fake_runner_mix_fail_itr_suite_level=fake_runner_src)
        self.testdir.chdir()

        with override_env(
            dict(
                DD_API_KEY="foobar.baz",
            )
        ):
            with mock.patch(
                "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_settings_api",
                return_value=_CIVisibilitySettings(False, False, False, False),
            ):
                subprocess.run(["python", "fake_runner_mix_fail_itr_suite_level.py"])
