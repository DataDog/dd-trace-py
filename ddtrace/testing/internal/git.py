"""Legacy re-export shim for a moved module.

``ddtrace.testing.internal.git`` moved to:

``ddtrace_testing.internal.git``

Keep the historical import path working for external consumers; import from
the new module instead.
"""

from ddtrace_testing.internal.git import Git as Git  # noqa: F401
from ddtrace_testing.internal.git import GitTag as GitTag  # noqa: F401
from ddtrace_testing.internal.git import GitUserInfo as GitUserInfo  # noqa: F401
from ddtrace_testing.internal.git import _GitSubprocessDetails as _GitSubprocessDetails  # noqa: F401
from ddtrace_testing.internal.git import (
    get_git_head_tags_from_git_command as get_git_head_tags_from_git_command,  # noqa: F401
)
from ddtrace_testing.internal.git import get_git_tags_from_dd_variables as get_git_tags_from_dd_variables  # noqa: F401
from ddtrace_testing.internal.git import get_git_tags_from_git_command as get_git_tags_from_git_command  # noqa: F401
from ddtrace_testing.internal.git import get_workspace_path as get_workspace_path  # noqa: F401
from ddtrace_testing.internal.git import is_ref_a_tag as is_ref_a_tag  # noqa: F401
from ddtrace_testing.internal.git import log as log  # noqa: F401
from ddtrace_testing.internal.git import normalize_ref as normalize_ref  # noqa: F401
