from collections import defaultdict
from contextlib import contextmanager
from unittest import mock

import ddtrace
from ddtrace.internal.ci_visibility import DEFAULT_CI_VISIBILITY_SERVICE
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


def _get_default_civisibility_ddconfig():
    new_ddconfig = ddtrace.settings.Config()
    new_ddconfig._add(
        "ci_visibility",
        {
            "_default_service": DEFAULT_CI_VISIBILITY_SERVICE,
        },
    )
    return new_ddconfig


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
