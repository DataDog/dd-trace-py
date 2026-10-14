# Moved to ddtrace_base.ext.git; this shim keeps the public import path working.
from ddtrace_base.ext import git as _git  # noqa: F401
from ddtrace_base.ext.git import *  # noqa: F401,F403
from ddtrace_base.ext.git import _build_git_packfiles_with_details as _build_git_packfiles_with_details  # noqa: F401
from ddtrace_base.ext.git import (
    _extract_clone_defaultremotename_with_details as _extract_clone_defaultremotename_with_details,  # noqa: F401
)
from ddtrace_base.ext.git import (
    _extract_latest_commits_with_details as _extract_latest_commits_with_details,  # noqa: F401
)
from ddtrace_base.ext.git import _extract_upstream_sha as _extract_upstream_sha  # noqa: F401
from ddtrace_base.ext.git import _get_rev_list_with_details as _get_rev_list_with_details  # noqa: F401

# Private helpers consumed through the historical path (star-import excludes them).
from ddtrace_base.ext.git import _GitSubprocessDetails as _GitSubprocessDetails  # noqa: F401
from ddtrace_base.ext.git import (
    _is_shallow_repository_with_details as _is_shallow_repository_with_details,  # noqa: F401
)
from ddtrace_base.ext.git import _unshallow_repository as _unshallow_repository  # noqa: F401
from ddtrace_base.ext.git import _unshallow_repository_with_details as _unshallow_repository_with_details  # noqa: F401
