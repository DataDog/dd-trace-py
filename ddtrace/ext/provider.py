"""
common CI providers
"""

import os
import re
from functools import partial

from ddtrace.vendor import attr

from .ci.services import (
    appveyor,
    azure_pipelines,
    bitbucket,
    buildkite,
    circle_ci,
    github_actions,
    gitlab,
    jenkins,
    teamcity,
    travis,
)
from . import ci, git
from ..internal.logger import get_logger

log = get_logger(__name__)

_PROVIDER_METADATA_KEY = "ddtrace.ext.provider"


def field(name, *args, **kwargs):
    kwargs.setdefault("default", None)
    kwargs.setdefault("metadata", {})
    kwargs["metadata"][_PROVIDER_METADATA_KEY] = name
    return attr.ib(*args, **kwargs)


_RE_BRANCH_PREFIX = re.compile(r"^refs/(heads/)?")


@attr.s(kw_only=True, eq=False, order=False, slots=True, frozen=True)
class Provider(object):

    # CI properties
    job_url = field(ci.JOB_URL)
    pipeline_id = field(ci.PIPELINE_ID)
    pipeline_name = field(ci.PIPELINE_NAME)
    pipeline_number = field(ci.PIPELINE_NUMBER)
    pipeline_url = field(ci.PIPELINE_URL)
    provider_name = field(ci.PROVIDER_NAME)
    workspace_path = field(ci.WORKSPACE_PATH)

    # Git properties
    branch = field(git.BRANCH, converter=attr.converters.optional(lambda value: _RE_BRANCH_PREFIX.sub("", value)))
    commit_sha = field(git.COMMIT_SHA)
    repository_url = field(git.REPOSITORY_URL)
    tag = field(git.TAG)

    def astags(self):
        """Add provider information to span."""
        return {_PROVIDER_LABELS[name]: value for name, value in attr.asdict(self).items() if value is not None}

    _registered_providers = (
        travis,
        circle_ci,
        jenkins,
        gitlab,
        appveyor,
        azure_pipelines,
        github_actions,
        teamcity,
        buildkite,
    )

    @classmethod
    def from_env(cls, env=None):
        """Build provider information from environment variables."""
        env = os.environ if env is None else env

        for provider in cls._registered_providers:
            if provider.match(env):
                try:
                    return cls(**provider.extract(env))
                except Exception:
                    log.error("could not create '{0}' provider info".format(provider.__name__))

        return cls()


_PROVIDER_LABELS = {f.name: f.metadata[_PROVIDER_METADATA_KEY] for f in attr.fields(Provider)}
