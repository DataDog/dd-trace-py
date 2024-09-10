import os
import typing as t


_PYTEST_SNAPSHOT_GITLAB_CI_ENV_VARS = {
    "GITLAB_CI": "true",
    "CI_COMMIT_AUTHOR": "TestFirst TestLast <First.Last@testtest.com>",
    "CI_COMMIT_MESSAGE": "test commit message",
    "CI_COMMIT_REF_NAME": "test.brancn/test_name",
    "CI_COMMIT_SHA": "c165eb71ef833b752783b5268f21521fd16f812a",
    "CI_COMMIT_TIMESTAMP": "2024-09-10T10:11:13+01:00",
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


def _get_pytest_snapshot_gitlab_ci_env_vars(new_vars: t.Dict[str, str]) -> t.Dict[str, str]:
    _env = os.environ.copy()
    _env.update(_PYTEST_SNAPSHOT_GITLAB_CI_ENV_VARS)
    _env.update(new_vars)
    return _env
