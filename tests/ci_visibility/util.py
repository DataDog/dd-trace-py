from collections import defaultdict
from contextlib import contextmanager
import os
import typing as t
from unittest import mock

import ddtrace
import ddtrace.ext.test_visibility  # noqa: F401
from ddtrace.internal.ci_visibility.git_client import METADATA_UPLOAD_STATUS
from ddtrace.internal.ci_visibility.git_client import CIVisibilityGitClient
from ddtrace.internal.ci_visibility.recorder import CIVisibility
from ddtrace.internal.ci_visibility.recorder import _CIVisibilitySettings
from tests.utils import DummyCIVisibilityWriter
from tests.utils import override_env


@contextmanager
def _patch_dummy_writer():
    original = ddtrace.internal.ci_visibility.recorder.CIVisibilityWriter
    ddtrace.internal.ci_visibility.recorder.CIVisibilityWriter = DummyCIVisibilityWriter
    yield
    ddtrace.internal.ci_visibility.recorder.CIVisibilityWriter = original


def _get_default_civisibility_ddconfig(itr_skipping_level: str = "tests"):
    new_ddconfig = ddtrace.settings.Config()
    new_ddconfig._add(
        "test_visibility",
        {
            "_default_service": "default_test_visibility_service",
            "itr_skipping_level": itr_skipping_level,
        },
    )
    return new_ddconfig


@contextmanager
def _mock_ddconfig_test_visibility(itr_skipping_level: str = "tests"):
    mock_test_visibility_config = mock.Mock()
    mock_test_visibility_config._default_service = "default_test_visibility_service"
    mock_test_visibility_config.itr_skipping_level = itr_skipping_level

    with mock.patch("ddtrace.config.test_visibility", mock_test_visibility_config):
        yield


@contextmanager
def set_up_mock_civisibility(
    use_agentless: bool = True,
    coverage_enabled: bool = False,
    skipping_enabled: bool = False,
    itr_enabled: bool = False,
    require_git: bool = False,
    suite_skipping_mode: bool = False,
    skippable_items=None,
):
    """This is a one-stop-shop that patches all parts of CI Visibility for testing.

    Its purpose is to allow testers to call CIVisibility.enable() without side effects and with predictable results
    while still exercising most of the internal (eg: non-API, non-subprocess-executing) code.

    It prevents:
    * requests to settings and skippable API endpoints
    * git client instantiation and use (skipping git metadata upload)

    It additionally raises NotImplementedErrors to try and alert callers if they are trying to do something that should
    be mocked, but isn't.
    """

    def _fake_fetch_tests_to_skip(*args, **kwargs):
        if skippable_items is None:
            if suite_skipping_mode:
                CIVisibility._instance._test_suites_to_skip = []
            else:
                CIVisibility._instance._tests_to_skip = defaultdict(list)
        else:
            if suite_skipping_mode:
                CIVisibility._instance._test_suites_to_skip = skippable_items
            else:
                CIVisibility._instance._tests_to_skip = skippable_items

    def _mock_upload_git_metadata(obj, **kwargs):
        obj._metadata_upload_status = METADATA_UPLOAD_STATUS.SUCCESS

    env_overrides = {
        "DD_CIVISIBILITY_AGENTLESS_ENABLED": "False",
        "DD_SERVICE": "civis-test-service",
        "DD_ENV": "civis-test-env",
    }
    if use_agentless:
        env_overrides.update({"DD_API_KEY": "civisfakeapikey", "DD_CIVISIBILITY_AGENTLESS_ENABLED": "true"})
    if suite_skipping_mode:
        env_overrides.update({"_DD_CIVISIBILITY_ITR_SUITE_MODE": "true"})

    with override_env(env_overrides), mock.patch(
        "ddtrace.internal.ci_visibility.recorder.ddconfig",
        _get_default_civisibility_ddconfig("suite" if suite_skipping_mode else "test"),
    ), mock.patch(
        "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
        return_value=_CIVisibilitySettings(
            coverage_enabled=coverage_enabled,
            skipping_enabled=skipping_enabled,
            require_git=require_git,
            itr_enabled=itr_enabled,
        ),
    ), mock.patch(
        "ddtrace.internal.ci_visibility.recorder.CIVisibility._fetch_tests_to_skip",
        side_effect=_fake_fetch_tests_to_skip,
    ), mock.patch.multiple(
        CIVisibilityGitClient,
        _get_repository_url=classmethod(lambda *args, **kwargs: "git@github.com:TestDog/dd-test-py.git"),
        _is_shallow_repository=classmethod(lambda *args, **kwargs: False),
        _get_latest_commits=classmethod(lambda *args, **kwwargs: ["latest1", "latest2"]),
        _search_commits=classmethod(lambda *args: ["latest1", "searched1", "searched2"]),
        _get_filtered_revisions=classmethod(lambda *args, **kwargs: "revision1\nrevision2"),
        _upload_packfiles=classmethod(lambda *args, **kwargs: None),
        upload_git_metadata=_mock_upload_git_metadata,
        _do_request=NotImplementedError,
    ), mock.patch(
        "ddtrace.internal.ci_visibility.recorder._do_request", side_effect=NotImplementedError
    ):
        yield


_PYTEST_SNAPSHOT_GITLAB_CI_ENV_VARS = {
    "GITLAB_CI": "true",
    "CI_COMMIT_AUTHOR": "TestFirst TestLast <First.Last@testtest.com>",
    "CI_COMMIT_MESSAGE": "test commit message",
    "CI_COMMIT_REF_NAME": "test.brancn/test_name",
    "CI_COMMIT_SHA": "c165eb71ef833b752783b5268f21521fd16f812a",
    "CI_COMMIT_TIMESTAMP": "2024-09-10T10:11:13+01:00",
    "CI_COMMIT_TAG": "v1.0.0",
    "CI_JOB_ID": "633358062",
    "CI_JOB_NAME": "test-job",
    "CI_JOB_NAME_SLUG": "test-job-slug",
    "CI_JOB_STAGE": "test-stage",
    "CI_JOB_URL": "https://test.test.io/Test/test-test/test-test/-/jobs/633358062",
    "CI_PIPELINE_ID": "43949931",
    "CI_PIPELINE_IID": "14726",
    "CI_PIPELINE_URL": "https://test.†est.io/Test/test-†est/test-test/-/pipelines/43949931",
    "CI_PROJECT_PATH": "Test/test-test/test-project-path",
    "CI_PROJECT_PATH_SLUG": "test-test-test-test-test-project-path",
    "CI_PROJECT_URL": "https://test.test.io/Test/test-test/test-test",
    "CI_REPOSITORY_URL": "https://test.test.io/Test/test-test/test-test.git",
    "CI_RUNNER_ID": "14727097",
    "CI_RUNNER_TAGS": '["runner:test-test-test-test"]',
}


def _get_default_os_env_vars():
    os_env = os.environ

    os_env_keys = {
        "PATH",
        "PYTHONPATH",
        "DD_TRACE_AGENT_URL",
        "DD_AGENT_PORT",
        "DD_TRACE_AGENT_PORT",
        "DD_AGENT_HOST",
        "DD_TRACE_AGENT_HOSTNAME",
    }

    return {key: os_env.get(key, "") for key in os_env_keys if key in os_env}


def _get_default_ci_env_vars(new_vars: t.Dict[str, str] = None, inherit_os=False, mock_ci_env=None) -> t.Dict[str, str]:
    _env = _get_default_os_env_vars()

    if inherit_os:
        _env.update(os.environ)

    if mock_ci_env:
        _env.update(_PYTEST_SNAPSHOT_GITLAB_CI_ENV_VARS)

    if new_vars:
        _env.update(new_vars)

    if "DD_TRACE_AGENT_URL" in _env:
        # We give the agent URL precedence over the host and port
        for agent_key in {"DD_AGENT_PORT", "DD_TRACE_AGENT_PORT", "DD_AGENT_HOST", "DD_TRACE_AGENT_HOSTNAME"}:
            if agent_key in _env:
                del _env[agent_key]

    return _env
