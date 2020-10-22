"""
common CI providers
"""

import os
import re

from ddtrace.vendor import attr

from . import ci, git
from ..internal.logger import get_logger

log = get_logger(__name__)

_PROVIDER_METADATA_KEY = "ddtrace.ext.provider"


def field(name, *args, **kwargs):
    kwargs.setdefault("metadata", {})
    kwargs["metadata"][_PROVIDER_METADATA_KEY] = name
    return attr.ib(*args, **kwargs)


_RE_BRANCH_PREFIX = re.compile(r"^refs/(heads/)?")


@attr.s(kw_only=True, eq=False, order=False, slots=True)
class Provider(object):

    # CI properties
    job_url = field(ci.JOB_URL, default=None)
    pipeline_id = field(ci.PIPELINE_ID, default=None)
    pipeline_name = field(ci.PIPELINE_NAME, default=None)
    pipeline_number = field(ci.PIPELINE_NUMBER, default=None)
    pipeline_url = field(ci.PIPELINE_URL, default=None)
    provider_name = field(ci.PROVIDER_NAME, default=None)
    workspace_path = field(ci.WORKSPACE_PATH, default=None)

    # Git properties
    branch = field(
        git.BRANCH, default=None, converter=attr.converters.optional(lambda value: _RE_BRANCH_PREFIX.sub("", value))
    )
    commit_sha = field(git.COMMIT_SHA, default=None)
    repository_url = field(git.REPOSITORY_URL, default=None)
    tag = field(git.TAG, default=None)

    # Deprecated properties
    _deprecated_commit_sha = field("git.commit_sha")

    @_deprecated_commit_sha.default
    def _default_deprecated_commit_sha(self):
        return self.commit_sha

    def astags(self):
        """Add provider information to span."""
        return {_PROVIDER_LABELS[name]: value for name, value in attr.asdict(self).items() if value is not None}

    @classmethod
    def from_env(cls, env=None):
        """Build provider information from environment variables."""
        from .ci import services

        env = os.environ if env is None else env

        for provider in services.PROVIDERS:
            if env.get(provider.ENV_KEY) is not None:
                try:
                    return cls(**provider.extract(env))
                except Exception:
                    log.error("could not create '{0}' provider info", provider.__name__)

        return cls()


_PROVIDER_LABELS = {f.name: f.metadata[_PROVIDER_METADATA_KEY] for f in attr.fields(Provider)}
