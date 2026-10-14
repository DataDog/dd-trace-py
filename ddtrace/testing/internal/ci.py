"""Legacy re-export shim for a moved module.

``ddtrace.testing.internal.ci`` moved to:

``ddtrace_testing.internal.ci``

Keep the historical import path working for external consumers; import from
the new module instead.
"""

from ddtrace_testing.internal.ci import PROVIDERS as PROVIDERS  # noqa: F401
from ddtrace_testing.internal.ci import CITag as CITag  # noqa: F401
from ddtrace_testing.internal.ci import TProviderFunction as TProviderFunction  # noqa: F401
from ddtrace_testing.internal.ci import extract_appveyor as extract_appveyor  # noqa: F401
from ddtrace_testing.internal.ci import extract_azure_pipelines as extract_azure_pipelines  # noqa: F401
from ddtrace_testing.internal.ci import extract_bitbucket as extract_bitbucket  # noqa: F401
from ddtrace_testing.internal.ci import extract_bitrise as extract_bitrise  # noqa: F401
from ddtrace_testing.internal.ci import extract_buddy as extract_buddy  # noqa: F401
from ddtrace_testing.internal.ci import extract_buildkite as extract_buildkite  # noqa: F401
from ddtrace_testing.internal.ci import extract_circle_ci as extract_circle_ci  # noqa: F401
from ddtrace_testing.internal.ci import extract_codebuild as extract_codebuild  # noqa: F401
from ddtrace_testing.internal.ci import extract_codefresh as extract_codefresh  # noqa: F401
from ddtrace_testing.internal.ci import extract_drone as extract_drone  # noqa: F401
from ddtrace_testing.internal.ci import extract_github_actions as extract_github_actions  # noqa: F401
from ddtrace_testing.internal.ci import extract_gitlab as extract_gitlab  # noqa: F401
from ddtrace_testing.internal.ci import extract_jenkins as extract_jenkins  # noqa: F401
from ddtrace_testing.internal.ci import extract_teamcity as extract_teamcity  # noqa: F401
from ddtrace_testing.internal.ci import extract_travis as extract_travis  # noqa: F401
from ddtrace_testing.internal.ci import get_ci_tags as get_ci_tags  # noqa: F401
from ddtrace_testing.internal.ci import log as log  # noqa: F401
from ddtrace_testing.internal.ci import register_provider as register_provider  # noqa: F401
