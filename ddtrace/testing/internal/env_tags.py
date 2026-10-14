"""Legacy re-export shim for a moved module.

``ddtrace.testing.internal.env_tags`` moved to:

``ddtrace_testing.internal.env_tags``

Keep the historical import path working for external consumers; import from
the new module instead.
"""

from ddtrace_testing.internal.env_tags import _PROVIDER_CI as _PROVIDER_CI  # noqa: F401
from ddtrace_testing.internal.env_tags import _PROVIDER_GIT_CLIENT as _PROVIDER_GIT_CLIENT  # noqa: F401
from ddtrace_testing.internal.env_tags import _PROVIDER_USER_SUPPLIED as _PROVIDER_USER_SUPPLIED  # noqa: F401
from ddtrace_testing.internal.env_tags import (
    _check_commit_sha_consistency as _check_commit_sha_consistency,  # noqa: F401
)
from ddtrace_testing.internal.env_tags import get_custom_dd_tags as get_custom_dd_tags  # noqa: F401
from ddtrace_testing.internal.env_tags import get_env_tags as get_env_tags  # noqa: F401
from ddtrace_testing.internal.env_tags import log as log  # noqa: F401
from ddtrace_testing.internal.env_tags import merge_tags as merge_tags  # noqa: F401
from ddtrace_testing.internal.env_tags import normalize_git_tags as normalize_git_tags  # noqa: F401
from ddtrace_testing.internal.env_tags import parse_tags_str as parse_tags_str  # noqa: F401
